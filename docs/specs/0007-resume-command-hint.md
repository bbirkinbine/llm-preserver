# 0007 — Resume Command Hint

**Status:** shipping
**Last updated:** 2026-07-13

## Goal

When a pull's shape was assembled interactively — the `discover`
navigation (search → tree → archive-mode → confirms) or `pull`'s
interactive file listing — the exact `pull` command that reproduces it
exists nowhere the user can retrieve it: not in shell history (only the
`discover` invocation is there), and not on screen. Interrupting a long
download (live use, 2026-07-13: 67 GiB whole-repo pull stopped at file
15 of 40) means re-driving the whole navigation to get back to the same
pull. Print a one-line, copy-paste-ready direct `pull` command **after
the confirmations succeed, before the first byte transfers**
(scrollback insurance), and print it **again as
the last line when the transfer is interrupted with Ctrl-C** — landing
directly above the next shell prompt, the closest reliable equivalent
to "hit up-arrow and the continue command is there". Because pull is
idempotent over already-archived files, the printed command doubles as
the resume command. (Verified 2026-07-13 during test-first: the skip matrix in
`pull_plan.plan_downloads` skips hash-matched files and adopts
crash-orphaned on-disk files by hash — "continue" is accurate, and a
changed-upstream file stays a hard integrity stop, so the resume never
papers over drift.)

## Success criteria

- On a discover-handoff pull, after the grouping and size
  confirmations succeed and before any file transfer begins, the CLI
  prints a line of the form
  `to continue this pull later: llm-preserver pull <repo_id> <abs-path> --whole-repo --model <creator>/<model>`
  (or with `--include` patterns in pick-files mode). Running that
  command verbatim resumes the same pull: same repo id, same archive
  path, same selection shape, same model directory. <!-- assumption:
  exact lead-in wording is implementer's choice; the invariant is one
  line, greppable, command quoted for the shell -->
- `pull` with the interactive file listing (no `--include`, no
  `--whole-repo`) prints the same style of hint once patterns are
  entered, carrying each pattern as a repeated `--include` flag.
- The printed command is shell-safe: patterns containing `*` or spaces
  are quoted (`shlex.quote`) so paste-and-run cannot glob or word-split.
- The archive path is always printed explicitly — as an **absolute
  path** — whether it arrived as the positional argument or via
  `$LLM_PRESERVER_ARCHIVE`: the hint must work in a shell without that
  variable set and from any working directory. Both origins are
  covered by tests (explicit relative path, env-var-only invocation).
  <!-- assumption: Typer does not expose argument-vs-envvar origin, so
  always printing the resolved path is also the only deterministic
  option -->
- The hint carries the **resolved canonical model directory** as
  `--model <creator>/<model>` once grouping is settled, so the continue
  command lands files in the same model folder as the interrupted pull
  — guaranteed, not dependent on answering the grouping prompt the same
  way twice. Consequence: the hint prints **after** the grouping and
  size confirmations succeed, still before the first byte transfers.
  This respects the 0006 adjudication (hub metadata never names an
  archive directory without a human yes): the hint replays a grouping
  decision the human already confirmed in this run.
  (Shipped as `pull_model`'s `on_transfer_start` callback, called with
  the resolved `<creator>/<model>` after all confirmations, before the
  first download; not called for adopt-only pulls.)
- No *transfer-start* hint is printed when the user already typed the
  full command shape themselves (`pull` with `--whole-repo` or
  explicit `--include`): their shell history already has it, and the
  extra line is noise. The Ctrl-C print is NOT gated the same way —
  see the next criterion. (Adjudicated in live use 2026-07-13: a
  resumed pull's second interrupt printed nothing, and the silence
  read as a miss; the interrupt line also carries the resolved
  `--model`, which the history entry lacks when grouping was answered
  at the prompt.)
- `--plan` runs print the hint too — a plan whose shape was navigated
  interactively has the same reconstruction problem. The plan-mode
  hint **omits `--model`**: plan mode records confirmations instead of
  asking them, so no grouping decision was human-confirmed, and the
  hint must not bake one in (0006 adjudication). It also omits
  `--plan` — the follow-up the user wants is the real pull.
- Ctrl-C during the transfer (`KeyboardInterrupt` after the pull shape
  is resolved) prints the hint as the **final line of output** on
  EVERY pull — interactively shaped or user-typed — then exits with
  code 130 (128 + SIGINT convention): the command sits immediately
  above the next shell prompt, no scrolling needed, and an interrupted
  transfer always ends by saying how to continue. The interrupt is
  never swallowed: no retry, no partial-state cleanup beyond what the
  transfer already guarantees, just print-and-exit.
  <!-- assumption: a try/except KeyboardInterrupt around the transfer
  call in run_pull, not a signal handler — simpler, and covers the
  laptop-close ^C case Brian described; Ctrl-C during navigation or
  prompts keeps today's behavior (nothing to resume yet) -->
- The hint reflects the other flags that shape the pull when present
  (`--role`, `--refresh-docs`); it never includes `--yes` — the re-run
  asks its own size confirmation. <!-- assumption: for the discover
  handoff these are all defaults today, so this mostly future-proofs
  the helper -->
- `docs/cli.md` documents the hint under both `discover` and `pull`
  (when it appears, that it is also the resume path after an
  interrupted download).

## Non-goals

- No `resume` subcommand, no state file, no persisted download queue —
  the scrollback line is the whole mechanism.
- No shell-history injection ("hit up-arrow and the command is
  there"): a child process cannot modify the parent shell's in-memory
  history, and writing `~/.zsh_history` / `~/.bash_history` directly is
  format-sensitive, shell-specific, and an unacceptable surprise for a
  CLI tool. The interrupt-time final-line print is the deliberate
  substitute (asked and adjudicated 2026-07-13).
- No skipping of the size confirmation on the re-run (`--yes` never
  rides in the hint) — only the grouping decision is replayed, because
  the human already made it; the size ask is cheap and re-verifies
  what remains to download.
- No attempt to reproduce the discover *navigation* (search query,
  tree hops) — only the resulting pull.

## Notes

- Implementation surface is small: the hint composes in
  `cli/pull_exec.py` once patterns are resolved (covers both callers);
  `discover` passes enough context already. Expected touch:
  `pull_exec.py`, tests, `docs/cli.md`.
- Related follow-up queued from the same live-use session: the
  interactive-listing TUI (three independent requests during 0006)
  would subsume some of this, but the hint is useful regardless — TUIs
  don't survive Ctrl-C either.
