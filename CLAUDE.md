# Project: llm-preserver

CLI tool that archives local LLMs for long-term offline use.
It pulls model artifacts (GGUF quants, full Hugging Face snapshots)
from model hubs into a runtime-independent local archive, and preserves
everything needed to still run them years later: weights, tokenizer/
config/chat template, license + model card, source URL, SHA256
checksums, a per-model record, and offline smoke tests. Design source:
"Local AI Model Preservation Plan" (Obsidian vault, Research/AI) —
summarized in `docs/specs/0000-product.md`.

Specs use **local-only numbering** (no GitHub issues): next number =
highest existing 4-digit prefix in `docs/specs/` + 1; branches are
`spec-NNNN-<slug>`. See `docs/specs/README.md` → "Local-only mode".

This repo is **public** on GitHub (`github.com/bbirkinbine/llm-preserver`)
or will become public after the first feature lands. Treat every change
as world-readable from commit #1.

An `AGENTS.md` stub sits alongside this file as a portable pointer for
non-Claude agents (Codex, Cursor, Gemini, etc.). `CLAUDE.md` is the
source of truth; `AGENTS.md` points back here.

> **Profiles.** This file ships in every bootstrap profile and describes
> the full workflow surface. Some slash commands and `docs/` files it
> mentions install only under the richer profiles (`--python-core` or
> `--full`); on a thinner install, treat those as "available if enabled"
> rather than guaranteed present.

**Standing rules are split between this file and `.claude/rules/`.**
Rules without a `paths` frontmatter load every session (git workflow,
commit style, public-repo hygiene); path-scoped rules load when matching
files are touched (Python code conventions, agent-legible code,
external-reference provenance). Both carry the same authority as this
file. When the human corrects a recurring mistake, encode the fix in
this file or the relevant rule in the same change — standing
instructions are the error log that compounds.

**Personal preferences stay out of the shared files.** Machine-local or
per-person overrides go in `CLAUDE.local.md` (instructions) and
`.claude/settings.local.json` (settings) — both gitignored by the
scaffold. This file, `.claude/settings.json`, and `.claude/rules/` are
team-shared; don't encode one person's editor, pace, or verbosity
preferences in them.

## Stack

- Python 3.12 (managed by `uv`)
- Planned libs (add via the `dependency-hygiene` skill as features land,
  not up front): `huggingface_hub` (downloads), Pydantic v2 (manifest /
  model-record models), Typer (CLI), stdlib `logging`
- pytest + pytest-asyncio
- ruff (lint + format) + mypy (strict)

## How to run things

- Install: `uv sync`
- Run app: `uv run llm-preserver` (CLI entry point; not implemented yet —
  defined by the first feature spec)
- Run tests: `uv run pytest`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Type-check: `uv run mypy src/`
- Single test: `uv run pytest path/to/test.py::test_name -xvs`

## Your role: orchestrator

You are the orchestrator for this repo — not only a coder. Your standing
job is to hold the high-level goal (the active spec) and drive the loop,
delegating focused or verbose work to subagents so your own context
stays clean enough to keep that goal in view. Context that fills with
raw test output and file dumps is context that has lost the plot.

Two distinct reasons to delegate — both matter:

- **Independence.** A reviewer that has already seen the implementation
  reasoning is not an independent reviewer. Hand the diff to a fresh
  subagent that has not.
- **Context hygiene.** Verbose work — codebase-wide searches, full test
  output, doc fetches, log scraping — burns the context you need for
  the goal. Push it into a subagent; only the summary returns.

Delegation decision rules — apply these without being asked:

| Situation | Route to |
| --- | --- |
| Task touches > 3 files, or you'd say "go figure out X and report back" | `/plan` — the `planner` subagent |
| About to implement anything past trivial | `/test-first` before any implementation code |
| Implementation done and `/review-check` is green | `/review` (and `/review-adversarial` on meaningful features) |
| Need full pytest output, a wide codebase survey, or doc fetches | A subagent — keep the verbose output out of your own context |
| "Who calls this?" / symbol nav on a *large* repo, with `serena` enabled | The `serena` MCP — query the index, don't grep-storm |
| A change would touch > 5 files | Stop and ask the human first |

**Re-anchor on the spec.** `docs/specs/NNNN-*.md` is the source of truth
for *what* you are building. Re-read the active spec at the start of
each phase, and any time the conversation has drifted from it. If your
context is getting long mid-feature, stop at a phase boundary and
`/clear` — see `WORKFLOW.md` → "Phase handoff".

**Verify before you report.** `/review-check`, the Stop-gate hook when
`--strict-hooks` is enabled, and CI mechanically verify the *code* — but
a claim the gate can't see ("the scrub worked", "these two files are
duplicates", "the service came back up") is only true once you have
proven it. Before you state an outcome,
run the concrete check that confirms it and show the output. "Looks
done" with no command behind it is a guess, and a confident wrong claim
costs more than the check would have.

Scale the loop to the task — heavyweight process on trivial work is its
own failure mode:

| Task size | The loop |
| --- | --- |
| Trivial — rename, typo, ≤ ~10 lines | Branch optional; skip spec and plan; just do it. |
| Small — one function, one file | Branch; spec = one sentence; skip `/plan`; `/test-first` still required. |
| Medium — 3–10 files | Full loop. |
| Large — refactor or new subsystem | Record the cross-cutting technical decision as an ADR (`/adr`) first; full loop; split into medium tasks; do not run it all in one session. |

## Workflow expectations (Spec → Plan → Test-first → Implement → Verify)

The human-facing walkthrough lives in `WORKFLOW.md`; the rendered
diagram is in `docs/workflow-diagram.md`. Honor each phase — don't run
open-ended.

**Autodrive between checkpoints.** When handed a spec to implement,
drive the loop yourself end to end — branch, `/test-first`, implement to
green, `/review-check` — without waiting for a per-phase prompt. Stop
and surface output at exactly two human checkpoints: after `/plan`
(before tests), and after `/review-check` passes (before commit). Never
commit on your own (see `.claude/rules/git-workflow.md`). If
`/test-first` or the gate shows the spec is wrong, stop and raise it
rather than coding around it. This applies to Medium and Large tasks;
Trivial and Small keep the scaled-down loop above.

**Handling review findings.** `/review` and `/review-adversarial` tag
each finding: `[auto-fix]` (mechanical — apply it, re-run
`/review-check`), `[no-op]` (informational), or `[ask-user]` (challenges
a deliberate spec decision or changes product behavior). During
autodrive, resolve `[auto-fix]` findings yourself; an `[ask-user]`
finding is a hard stop — surface it verbatim and wait. The one exception
is an explicit instruction to run unattended ("just ship it"), which is
standing consent to resolve `[ask-user]` findings too.

- **Spec.** Before any non-trivial work, write a short spec under
  `docs/specs/NNNN-<feature>.md` (see `docs/specs/README.md` for the
  numbering, local-only mode, required sections, and
  `## External references` provenance). One paragraph minimum: goal,
  success criteria, non-goals. When the feature has already been discussed
  in-session, `/spec` can draft the body from that discussion (marking any
  assumptions inline); it still stops for the human to review and edit
  before `/plan`.
  On ambiguous features, use `/scope-check` before and `/clarify` after
  the draft if those commands are installed. Product-level direction
  lives in `docs/specs/0000-product.md` (written by the `/product-spec`
  interview, if present); feature specs
  link to it rather than restating product rationale. Cross-cutting
  *technical* decisions — ones costly to reverse that several features
  inherit (storage engine, async/sync boundary, public API shape, auth
  model) — go in an ADR (`/adr`, see `docs/adr/README.md`), not the
  feature spec.
- **Plan.** For tasks that touch > 3 files: `/plan` first. Review the
  plan before any writes happen.
- **Test-first.** Tests come before implementation. `/test-first` writes
  failing pytest tests from the spec; show the failing-test output.
  Only then implement. If installed, `/analyze` after tests cross-checks
  spec ↔ tests coverage before the implementation work starts.
- **Implement.** You must already be on a feature branch (see
  `.claude/rules/git-workflow.md`). Write the minimum code to make the
  tests pass. External-authority values follow
  `.claude/rules/python-code.md` → "External-reference provenance".
- **Usage docs ride the feature.** `docs/cli.md` is the user-facing
  command reference; any branch that adds or changes a CLI command,
  flag, or exit code updates it (and the README quick start when the
  surface changes) in the same change. `--help` stays the generated
  source of truth for syntax; `docs/cli.md` explains behavior
  (idempotency, auth, fault domains) that `--help` can't.
- **Docs sweep before the spec is done** (Brian, 2026-07-12). After
  implementing each spec, sweep every doc the change could have
  touched — `docs/cli.md`, README, `docs/data-structures.md`,
  `WORKFLOW.md`, other `docs/` files, and stale references in
  TODO.md or older docs — updating impacted ones and creating new
  docs when the feature warrants (e.g. a new user-facing surface
  with behavior `--help` can't carry). A grep for the old flag/term
  across `docs/`, `README.md`, and `TODO.md` is the minimum check —
  and grep for the spec number too: creating spec NNNN consumes the
  number at creation, not at merge, so the next-spec pointers
  (TODO.md's "Next spec" header, the CLAUDE.md open-work line)
  update on the same branch (Brian, 2026-07-13).
- **Close-out rides the feature PR** (Brian, 2026-07-13). The
  status-to-shipped flip, the TODO.md Shipped entry, and the
  CLAUDE.md session notes go on the feature branch as its last
  commit once the PR is open (the PR number exists from that point;
  "shipped" in a merged PR is self-fulfilling). Do not spawn a
  separate close-out chore PR just for these.
- **Verify.** Run `/review-check` (ruff lint, ruff format, mypy,
  pytest), then `/review` on the diff; `/review-adversarial` as well on
  meaningful features when installed. Add `/security` and/or
  `/performance` if the opt-in subagent and command are installed and the
  diff trips its triggers. If the product itself contains an LLM/AI
  surface and the `evaluator` subagent plus `/eval` command are installed,
  `/eval` is part of Verify too — it judges output quality a test can't
  assert (`docs/evals.md`). Deterministic projects ship no LLM surface
  and skip it.
- **Bug fixes — confirm the cause before the fix.** Reproduce the
  failure first, then have `/test-first` write a test that fails *for
  the reason you believe is the cause*. A reproducing test that fails
  for a different reason means the diagnosis is wrong — fix the
  diagnosis, not the symptom. Don't commit until that test goes
  red → green.
- **Phase handoff on multi-day features.** Append a `## Phase handoff`
  section to the spec at each phase boundary, then `/clear` and resume
  fresh — see `WORKFLOW.md` → "Phase handoff".
- If a change would touch > 5 files, stop and ask first.

## Code navigation (optional: `serena` MCP)

Default to the built-in tools — `grep` / `glob` / `read`, with a
subagent for wide surveys. **Do not enable `serena` on a fresh or small
repo.** Enable it only once a repo is large or long-lived — when the
agent burns most of its turns re-reading files to rebuild the same
structural map every session. Setup, verification, update, teardown:
`docs/serena-setup.md` (installed with `--full` / `--advanced-docs`).

## Subagents (in `.claude/agents/`)

- `planner` — read-only; produces a plan in markdown.
- `test-first` — writes failing pytest tests from a spec; never writes
  implementation.
- `reviewer` — independent diff reviewer; checks spec match, test
  quality, edge cases, file size, public-repo hygiene.
- `reviewer-adversarial` — same independence, adversarial framing;
  argues against the change. Pair with `reviewer` on meaningful
  features; same output schema for side-by-side reading.
- `security-reviewer` — **enabled** (with the `/security` command): this
  tool talks to model hubs over the network, handles HF auth tokens,
  and consumes untrusted upstream metadata/files. Run `/security` in
  Verify on any diff touching downloads, auth, token handling, archive
  writes, or parsing of hub-supplied data.

Still opt-in (copy from the scaffold's `.claude/agents/optional/`):
`performance-reviewer` (hot paths, DB queries on user-sized data,
async, load) and `evaluator` (only when the *product* contains an
LLM/AI surface — authors and runs evals that judge output quality
against a rubric; see `docs/evals.md`).

## Skills (in `.claude/skills/`)

- `python-module-split` — auto-invoked when a `.py` file approaches 300
  lines; splits a module into a package preserving the public API.
- `python-docstrings` — auto-invoked when a public symbol is added or
  touched; enforces Google-style docstrings.
- `dependency-hygiene` — auto-invoked when `pyproject.toml` adds a dep;
  surfaces maintenance/license/advisory checks before the dep lands.

## Slash commands (in `.claude/commands/`)

Bootstrap profiles decide which commands are installed. `--minimal`
includes only `/spec`, `/plan`, `/test-first`, `/review-check`, and
`/review`; `--python-core` adds the attended workflow helpers;
`--full` adds the optional-reviewer stubs. `docs/project-types.md` maps
each profile to the commands, agents, and skills it ships, and when to
reach for each.

| Command | Purpose |
| --- | --- |
| `/product-spec [name]` | Optional: interview to create/refresh `docs/specs/0000-product.md` (the product-level spec) |
| `/scope-check <desc>` | Optional pre-spec: five forcing questions on ambiguous features |
| `/spec <name>` | Create `docs/specs/NNNN-<slug>.md` scaffold; stops for human edit |
| `/clarify [spec]` | Interrogate a draft spec's underspecified areas; writes answers back in |
| `/adr <title>` | Record an architecture decision at `docs/adr/NNNN-<slug>.md` (independent numbering; for large/cross-cutting technical choices) |
| `/specs-status [filter]` | Refresh the `## Status` dashboard in `docs/specs/README.md` and print the status table in chat |
| `/plan [spec]` | Invoke `planner` on the spec |
| `/test-first [spec]` | Invoke `test-first` |
| `/analyze [spec]` | Read-only consistency check: spec ↔ tests ↔ diff ↔ standing rules |
| `/review-check` | Local quality gate (ruff, format, mypy, pytest); refuses to pass on failure |
| `/review [range]` | Invoke `reviewer` on the diff |
| `/review-adversarial [range]` | Invoke `reviewer-adversarial` on the same diff |
| `/security`, `/performance [range]` | Opt-in reviewers, if installed |
| `/eval [spec]` | Opt-in: author/run the eval suite for an LLM/AI feature, if `evaluator` is installed |

## Hooks and guardrails

Defense in depth, soft to hard — each is one layer, none is a guarantee:

- **Permission deny rules** (`.claude/settings.json` →
  `permissions.deny`) block the Read tool on `.env` / `.env.*` files and
  `*.pem` / `*.key` material. They gate the Read tool only — Bash can
  still print a file, so the behavioral rule in
  `.claude/rules/public-repo-hygiene.md` ("Secrets must not enter the
  context window") is the other half of this layer.
- **Status line** (`statusline.sh`) keeps branch · model · context %
  visible under the prompt every turn — the branch discipline and the
  session-hygiene thresholds (`WORKFLOW.md` → "Session hygiene") both
  lean on it.
- **SessionStart** (`branch-check.sh`) warns when a session opens on
  `main`.
- **PreToolUse** (`block-destructive.sh`) blocks unrecoverable Bash
  commands (`rm -rf /`, `git clean -fd`, `mkfs`, `dd of=/dev/`, …). To
  bypass for a legitimate need, run the command outside the session or
  temporarily disable the hook — do not edit the deny-list for a
  one-off. OS-level sandboxing (`/sandbox`) and permission modes sit
  above this layer; prefer them for unattended runs.
- **PostToolUse** runs `ruff format` after every Edit/Write by default;
  `/review-check` and CI remain the hard gates. If bootstrap was run with
  `--strict-hooks`, this hook also runs `ruff check` + `mypy` after every
  edit. A second PostToolUse hook (`specs-status.sh`) regenerates the
  `## Status` dashboard in `docs/specs/README.md` whenever a spec file
  under `docs/specs/` is created or edited, so the struck-through/live
  status list stays current without a manual step. It only ever rewrites
  its own generated block; the spec `**Status:**` lines remain the source
  of truth.
- **PreCompact** injects a reminder to preserve the active spec path,
  branch, and modified-file list through compaction.
- **Stop** (`gate-on-stop.sh`, strict-hooks only) blocks ending a turn
  while `src/` has pending changes and ruff/mypy/pytest are red —
  `/review-check` made mechanical. Note: Claude Code overrides a Stop
  hook after 8 consecutive blocks, so the gate is a strong nudge, not an
  unbounded guarantee; `/goal` and a fresh verification subagent sit
  above it (see `WORKFLOW.md` → "The completion ladder").
- **pre-commit** blocks commits on `main` (`no-commit-to-branch`) and
  scans for secrets (`gitleaks`, `detect-private-key`). A `commit-msg`
  hook (`strip-ai-attribution.sh`) is the mechanical backstop for the
  no-AI-attribution rule in `.claude/rules/commit-style.md`: it strips
  any `Co-Authored-By: Claude` trailer or "Generated with Claude Code"
  footer from the message. `uv run pre-commit install` wires both hook
  types (`default_install_hook_types` in `.pre-commit-config.yaml`).
- **CI** (`.github/workflows/ci.yml`) runs the full gate on every PR —
  the non-skippable backstop. A second `audit` job runs `pip-audit`
  against the locked dependency tree, so a known CVE fails the PR;
  Dependabot (`.github/dependabot.yml`) opens the weekly update PRs that
  fix them. For an unfixable transitive advisory, ignore it explicitly
  with `pip-audit --ignore-vuln GHSA-...` and a comment.

## Beyond a single session

Parallel agents in git worktrees, agent teams, and unattended runs
(`/goal`, `/loop`, `/sandbox` — Claude Code built-ins, not commands in
`.claude/commands/` — and autonomous loops) are covered in
`docs/parallel-agents.md` when the full/advanced docs are installed. The
normal default remains tier 1: one attended session driving the loop;
parallelize only with partitioned file ownership.

## Don't-touch list

- `pyproject.toml` `[tool.uv]` section — ask first
- The model archive itself (wherever `llm-preserver` is pointed at,
  e.g. a NAS path or `~/models/`) — the tool's *output* is irreplaceable
  data; never delete, move, or "clean up" archive contents. Tests use
  tmp dirs, never a real archive.

## Open work / current state (updated 2026-07-13, end of session 9)

- **Specs 0001, 0003, 0004, 0005, 0006, 0007 are all merged; 0008
  shipped (PR #11).** The loop is live-verified end to end: discover
  (name → tree → pull) or pull by exact id → advisories/--plan →
  status/show, with a printed resume command surviving any
  interrupted transfer.
- **Session 9 (2026-07-13, medium-tier, PR #11): spec 0008
  `--hf-logging`.** Vendor-telemetry passthrough on pull/discover:
  `RUST_LOG=info` set at command startup only when unset (an
  inherited filter wins, with one notice line naming it — an
  accidentally empty `RUST_LOG` must not read as a broken flag),
  `huggingface_hub` raised to exactly info via its typed
  `set_verbosity`; debug (cURL/request URLs) unreachable by any flag
  combination; no self-identification to the hub. Three checkpoint
  adjudications: the override notice; the 0007 resume hint replays
  `--hf-logging` (not `--verbose` — the hint serves the
  stalled-transfer scenario the flag exists for); one activation
  line because healthy transfers are provably silent at info (a
  control run with `RUST_LOG=info` exported externally was equally
  silent). Hard-won facts: `hf_xet` imports lazily inside the
  download path — a tripwire test pins it, since an eager import at
  startup would run the Rust env read before the flag's write; an
  invalid `RUST_LOG` kills `uv run` itself before Python starts (uv
  is a Rust binary reading the same variable; installed entry points
  are unaffected); tracing tolerates invalid filters silently; Xet
  console lines go to stdout (vendor default, no `with_writer`)
  while tool and Python client diagnostics go to stderr. Live
  hygiene sweep of flag-on output: clean (recorded in the spec).
  Stall-time rendering is the one deferred criterion — the first
  real stall (or a mid-transfer wifi toggle) closes it. 455 tests.
- **Session 8 (2026-07-13, small-tier, PR #9): spec 0007
  resume-command hint.** Interactively shaped pulls print the exact
  direct `pull` command (absolute path, quoted patterns, confirmed
  grouping as `--model`) after the confirms; Ctrl-C during any
  transfer reprints it as the final line, exit 130. Two live-use
  adjudications: (1) the Ctrl-C print is unconditional — a resumed
  (user-typed) pull's interrupt printing nothing read as a miss;
  (2) shell-history injection is impossible (child processes cannot
  touch the parent shell's history), so the interrupt-time final-line
  print is the deliberate substitute. Security review added
  `looks_like_repo_id` validation at hint composition (quoting cannot
  defuse an argv token — a hub id like `--yes` must not become a
  flag on paste) and sanitize-before-quote. Queued in TODO: extend
  `clean_text` to bidi/zero-width chars (Low/theoretical).
  `pull_exec.py` → `pull_exec/` package (plumbing/prompts/flow).
  README documents `uv tool install --editable .` — the hint assumes
  the CLI is on PATH, and `uv run` only works from the project dir.
  436 tests.
- **Session 7 (2026-07-13, trivial-tier, PR #8):** `-h` now works as
  a help alias on every command (`help_option_names` on the root
  Typer app propagates to subcommands); the Typer-provided
  `--install-completion` / `--show-completion` flags were kept and
  documented in `docs/cli.md`. Second CI-only rich-ANSI test failure
  became a standing rule: `.claude/rules/python-code.md` → "Asserting
  on CLI output in tests" (`click.unstyle()` + the FORCE_COLOR repro
  recipe). 419 tests.
- **Spec 0006 shipped 2026-07-13 (PR #7, rebase-merge, four
  commits):** the `discover` command — hub search verbatim (relevance
  order stated on screen) → model-tree navigation (ancestry ladder
  root-at-top, your-path breadcrumb with stack-pop, stable `0` pull
  key, `m` pages every relation, frame-separator rules, archive-mode
  choice pick-files vs whole-repo) → the unmodified pull flow via the
  extracted `cli/pull_exec` core with `model=None` (pull's
  confirm-gated grouping decides the home — the review round's
  unanimous finding: hub metadata must never name an archive
  directory without a human yes). Hub seam grew search_models /
  list_children / model_summary; `hub.py` split into the `hub/`
  package. 417 tests. FIFTEEN live-use adjudications from Brian's
  manual testing shaped the UX — the manual-verify loop out-found
  the review round on usability; budget real time for it.
- Hard-won facts from 0006: the hub's model-tree index (`baseModels`
  expand) is *rename-resolved* by the hub while card `base_model`
  goes stale — two sources, different freshness; pulls now
  rename-resolve the declared base with one disclosed light call
  (second sanctioned exception to one-metadata-call, after the
  adapter-config fetch). The 0000 non-goal was revised (2026-07-13):
  deterministic discovery is in scope — the invariant is no LLM and
  no tool judgment, not "no search". Pre-commit hooks choke on mixed
  staged/unstaged state (ruff autofix conflicts on stash restore) —
  commit with `git stash push --keep-index --include-untracked`,
  which also proves each commit green in isolation.
- **Spec 0009 verify is in flight on `spec-0009-verify`** (branch
  open 2026-07-13): the whole-archive fixity audit — complete vs
  valid, `--quick`, `--model` scoping, exit-code cron contract,
  atomic manifest regeneration.
- **Next spec (0010): pick from TODO.md** — runtime views (0002,
  unblocked), managed remove/retire, smoke test, or the
  interactive-listing TUI (three independent live-use requests
  during 0006). Also queued from live use: goal-definitive archiving
  (capability report in `status`), file-kind dictionary, live-hub
  canary (0000 roadmap).
- Specs: `0000` evergreen (revised 2026-07-13); `0002` runtime views
  (draft, unblocked); 0005/0006/0007/0008 shipped.
- Design stance (revised with 0000, 2026-07-13): no LLM and no tool
  judgment inside the tool — deterministic product, so no `/eval`.
  Discovery may pass through hub search/tree facts for the human to
  pick from, but every pull still targets an exact repo id the
  human chose, and the tool never ranks, selects, or auto-pulls.
- Public-facing framing rule: avoid "against future access
  restrictions" / threat-prediction phrasing in repo docs — neutral
  durability/offline language only (Brian, 2026-07-09).
- Personal-context rule (2026-07-11): homelab/infrastructure
  specifics (storage hosts, mount paths, hardware inventory, private
  note names) stay out of repo files — including these session
  notes. Generic phrasing only ("a NAS path", "a 24GB GPU").
