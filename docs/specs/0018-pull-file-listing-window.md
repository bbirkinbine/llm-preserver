# 0018 — Pull File Listing Window

**Status:** shipped (PR #31)
**Last updated:** 2026-08-12
**Depends on:** 0015

## Goal

`pull`'s interactive file listing prints every file in the repo, one
line each, with no window and no paging. Spec 0015 fixed exactly this
shape in `discover`'s two stages and named this listing as explicitly
out of scope ("**No change to pull's file listing**, which has a
related long-scroll problem"). The queued follow-up is now due.

Live-use trigger (2026-08-12): `discover 'kimi k3'` → pick
`unsloth/Kimi-K3-GGUF` → `1 = pick files` printed **171 file rows plus
a header into a 24-line terminal**. The discover stages the human just
walked through were windowed and footered; the handoff to the pull flow
dropped them into the unwindowed wall the previous stage existed to
prevent. Under `screen`'s default scrollback the top of that listing is
not merely off-screen, it is unrecoverable — the same failure mode
0015 documents.

Windowing alone is the wrong fix here, because this stage is not a
numbered pick. It prompts for a **glob pattern**, and the fact that
decides the pattern is *which quant directories exist*, not the 166
individual shard filenames. Paging a 171-row list eighteen rows at a
time means nine keypresses to learn the repo offers ten quants. So the
default frame becomes a **directory roll-up** — one line per top-level
directory with its file count and total size, root-level files listed
individually — and the complete per-file listing stays one keypress
away, itself windowed with `m`/`b`.

The roll-up summarizes; it never replaces. Spec 0018's abandoned
predecessor (companion advisory consequence, dropped 2026-08-07) failed
because grouping *suppressed* facts and hid a genuinely missing
projector. The guard here is structural: every file remains reachable
and individually listed behind `f`, no file is ever filtered out of the
expanded view, and the roll-up's counts and sizes are sums of exactly
what is there.

## Success criteria

- **No frame exceeds one screen on a TTY.** The roll-up frame and every
  expanded-listing frame are sized from the terminal height less fixed
  chrome, counting *physical* lines — a row wider than the terminal is
  charged the lines it wraps to, and so is the chrome. This is 0015's
  hardest-won criterion (a logical-line budget promised one screen and
  delivered 39-45 physical rows before its review round caught it) and
  it is re-asserted, not re-derived: there is **one** physical-line
  rule in the codebase, not two. The plan round found that `fit_rows`
  and `row_line_cost` cannot be called as-is from here; the rule is
  shared by extracting it instead — see the adjudications below.
- **Non-TTY output is unchanged, byte for byte.** A piped or redirected
  run prints the full flat listing exactly as it does today — no
  roll-up, no window, no footer, no key line. A pipe has no scroll
  problem; it has a file. This is the 0009/0015 precedent (TTY-only
  rendering, non-TTY output untouched) and it is what keeps every
  existing `prompt_for_selection` test valid without modification.
- **The roll-up is complete and arithmetically honest.** Every file in
  `RepoInfo.files` is accounted for in exactly one roll-up line. A
  directory line's count is the number of files beneath it and its size
  is their sum. The header states the repo-wide file count and total.
  Nothing is filtered, suppressed, or elided — the only information the
  roll-up withholds is individual filenames, and `f` supplies those.
- **The roll-up only replaces the flat listing when the flat listing
  would overflow.** A repo whose files fit the window on a TTY prints
  the flat per-file listing as today. The roll-up is the answer to a
  wall, so it appears only when there is one. A repo with no
  subdirectories therefore never rolls up on a fitting screen, and when
  it does not fit, its roll-up is its flat listing (root files list
  individually), so the overflow path degrades to paging alone.
- **Hub order is preserved.** Roll-up lines appear at the position of
  their group's first file in `info.files`; the expanded listing is
  `info.files` order verbatim. No sorting, no ranking, no
  most-relevant-first — the 0000 and 0006 invariants (hub facts in hub
  order, no tool judgment) apply here as they do to every other
  listing.
  <!-- assumption: the option preview shown at the design checkpoint put
  directories before root files, which reads well but reorders hub
  output. Pinned as hub order instead, because "no tool judgment" is a
  standing invariant and reordering is the kind of small helpfulness
  that becomes a rule nobody can state later. Say so if you want the
  preview's ordering — it is a one-line change here, not a redesign. -->
- **`f` expands to every file, paged.** From the roll-up frame, `f`
  renders the complete per-file listing one terminal-sized window at a
  time, with `m` for the next window and `b` for the previous — the
  same two keys, same meanings, same footer grammar as 0015. `m` is
  withdrawn from the footer and the prompt at the end of the listing;
  `b` is offered only when an earlier window exists. No network call
  happens in either direction: the file list came from the single
  metadata call the pull already made (spec 0003), so the whole listing
  is in hand before the first frame renders.
- **`s` returns to the roll-up** from the expanded listing, so the two
  frames are a toggle and neither is a dead end.
- **A pattern can be typed from either frame.** The prompt is the same
  pattern prompt in both; only the offered keys differ, and they are
  advertised offered-keys-only in 0015's style (quit last). Typing a
  pattern ends the stage and returns it, from the roll-up or from
  window four of the expanded listing alike — the human should not have
  to page back to a particular frame to answer.
- **Keys and patterns are unambiguous.** A bare single-character input
  matching an offered key is that key; everything else is a pattern
  list. The documented pattern idiom already starts patterns with `*`
  ("The leading `*` matters" — `prompts.py:79`, from a live mispull on
  2026-07-12), so no realistic pattern collides. Accepted consequence,
  documented in `docs/cli.md`: a file named exactly `f` or `s` is not
  selectable by typing that bare character. The escape hatch is the
  trailing comma — `f,` is the pattern list `["f"]` — not `*f`, which
  matches the full repo path and would select every `.gguf` too
  (review round corrected this claim, 2026-08-12).
- **Degraded streams do not crash.** A detached, closed, or
  non-terminal stdout resolves to the non-TTY path rather than raising
  — the 0011/0012 class, which 0015's review round hit in exactly this
  helper (`resolve_window_size` tracebacked before any output, turning
  a silent run into a crash). Reused code inherits the fixed behavior;
  a regression test pins it at this call site too.
- **Existing pull behavior is untouched.** The returned value is the
  same `list[str]` of stripped patterns; empty input still returns
  `[]`; the `— vision projector` style kind notes from `COMPANION_RULES`
  still annotate individual files in the expanded listing and in a
  fitting flat listing; `clean_text(..., single_line=True)` still
  scrubs every rendered line, including the new roll-up lines and
  header, since directory names are hub-supplied and same-trust-class
  as file paths.

## Non-goals

- **No TUI.** No `textual`, no `prompt_toolkit`, no new dependency, no
  alternate screen buffer, no arrow keys, no raw-terminal input. Output
  stays plain `typer.echo` lines a pipe can read. Same non-goal 0015
  took, same reason.
- **No type-to-filter.** Narrowing the listing by typing a fragment
  stays queued in TODO.md as the remaining half of the interactive
  listing item. Decided at the design checkpoint (2026-08-12): it needs
  input handling the codebase does not have, and the wall is the
  problem being reported.
- **No match preview or confirmation loop.** Showing what a typed
  pattern matched before accepting it was offered and declined at the
  same checkpoint. It is a good idea and a separate spec; the pull plan
  report (spec 0005) and `--plan` already cover the "what did I just
  ask for" question after the fact.
- **No jump-to-window pick** (`p3`, `g20`, …), matching 0015.
- **No user-facing page-size flag**, matching 0015.
- **No kind notes on roll-up lines.** `COMPANION_RULES` classifies
  filenames, not directories; inferring a directory's kind from its
  members is exactly the tool judgment the 0000 invariant excludes, and
  the abandoned advisory spec is the standing evidence for what that
  costs. Kind notes stay on individual file rows.
- **No change to `discover`'s stages**, beyond relocating the shared
  sizing helper (below), which is a pure move.
- **No change to selection, planning, or download.** This spec changes
  what the human reads before typing a pattern, and nothing after.

## External references

**None — original.** Every value here is the tool's own UX: the frame
shapes, the key alphabet, and the roll-up grammar are defined by this
repo. Terminal height and width come from stdlib
`shutil.get_terminal_size`, a library call rather than a cited external
table — and it is read only after the `isatty` gate, for the reason
0015 pinned live (it consults `LINES`/`COLUMNS` *before* the OS, so
reading it first would let an exported `LINES` change piped output).
The hub metadata fields consumed here (`path`, `size` on `RepoFile`)
were pinned in spec 0003.

## User-visible strings

Pinned here because `docs/cli.md` and the CLI tests must agree with the
renderer verbatim.

- Roll-up header: `files in {repo_id} ({n} files, {total}):` where
  `{total}` is `human_size` of the summed sizes. When any file's hub
  size is None, the total reads `at least {total}` — a floor is honest,
  a wrong sum is not.
- Roll-up directory line: the existing two-column shape, right-aligned
  size then name, with the count trailing at a fixed column:
  `  {size:>10}  {name + "/":<26}{k} files`. A name longer than the pad
  pushes the count right rather than truncating — a truncated directory
  name is not a typeable pattern.
- Roll-up root-file line: identical to today's flat line, kind note
  included.
- Roll-up key line: `f = list every file (paged), q = quit`.
- Expanded-listing footer: `showing {first}-{last} of {n}`, then
  ` — more (m)` when rows remain, then ` · back (b)` when an earlier
  window exists. Unlike 0015's footer, `{n}` here is a true total: the
  whole file list is in hand from the metadata call, so nothing is
  being discovered as the human pages.
- Expanded-listing key line: offered keys only, quit last —
  `m = more, b = back a page, s = summary, q = quit`.
- Pattern prompt: unchanged verbatim from `prompts.py:81` —
  `files to pull (comma-separated patterns, e.g. *Q4_K_M* or
  *.gguf,*mmproj*)`.
- `q` at either prompt aborts the pull the way an aborted confirm does
  today (exit path unchanged).

## Adjudications (plan round, 2026-08-12)

The planner surfaced five points this spec left implicit or got wrong.
Decided rather than deferred, so the implementation has one reading.

- **`fit_rows` and `row_line_cost` are not reusable, and the criterion
  claiming they are was wrong.** `fit_rows` reads `row.summary.relation`
  off a `NumberedRow`; `row_line_cost` closes over a renderer that
  emits `  {n}. {repo_id}{facts}`. Neither shape survives contact with
  a file row. Only the physical-line arithmetic is common. Resolution:
  a new neutral `src/llm_preserver/text_window.py` holds
  `wrapped_height(text, width)` (moved from `discover_render.py`) and a
  new `fit_by_cost(costs, start, budget)`; `fit_rows` becomes a thin
  adapter that builds its cost list and calls it. The intent of the
  criterion stands — **one** physical-line rule, not two — but it is
  met by extraction, not by calling the existing functions. The
  existing `fit_rows` tests are the neutrality proof and must pass
  untouched; if they need edits, the extraction is not behavior-neutral
  and is wrong.
- **The pipe needs "no window", not "a 20-line window".**
  `resolve_window_size` answers `NON_TTY_WINDOW_ROWS = 20` for a
  non-terminal, which is right for discover and wrong here — this spec
  requires the full flat listing on a pipe. The flow branches on
  interactivity *before* consulting any budget, so `_interactive`
  becomes public as `is_interactive(stream)`. Using
  `resolve_window_width(...) is None` as a TTY proxy would work and
  would read as a bug to the next session.
- **The fit predicate is charged against the flat frame's chrome.**
  Three frames have three chromes (flat: header + prompt; roll-up:
  header + key line + prompt; expanded: header + footer + key line +
  prompt). The predicate decides whether to print the *flat* frame, so
  it must be sized against the flat frame's chrome, and — following
  `tree_chrome_lines` — against the widest form each chrome line can
  take. The pattern prompt is 74 characters, 76 as click renders it
  with `": "`, so it costs two rows below 76 columns; and the footer's
  widest form has *both* indices at their largest, since `first` grows
  as the human pages. Charging either at its narrowest overflows the
  screen — measured, both errors, at 42 columns (review round).
- **One fallback, not two.** The frame chain is: flat listing fits →
  print it, unchanged, no keys; else roll-up has at least one directory
  line and itself fits → roll-up frame offering `f`; else → open
  directly on the paged expanded listing, with `s` offered only when a
  roll-up frame exists to return to. This answers both gaps the planner
  found — a repo of 171 root files with no subdirectories, and a
  roll-up of 200 directories that overflows in its own right — with the
  same rule, and it means no frame ever needs the roll-up windowed.
- **A fitting listing prints no key line, so `q` stays a pattern
  there.** Offered-keys-only is already this repo's idiom (0015), and
  the alternative — reserving five characters on every repo including
  the ones with no wall — changes behavior where there is no problem to
  fix. Consequence, documented in `docs/cli.md`: the same keystroke is
  a key on an overflowing repo and a pattern on a fitting one. Keys are
  matched on the raw stripped input *before* the comma split, so `f,`
  and `f, *.gguf` are pattern lists, never the key.

**Scope correction the planner forced:** `q` does not have an "exit
path unchanged" to inherit, because there is no `q` today and this
prompt has no `except typer.Abort` — EOF here escapes to click's
standalone handler as `Aborted!` / exit 1, which is the undocumented
exit 1 that `confirm_or_stop`'s own docstring says scripted pulls must
never die with. That is a **pre-existing defect, not this spec's**.
`q` resolves to `PullUserError` → exit 2, matching `confirm_or_stop`'s
posture that user input is the exit-2 domain. The EOF path is left
exactly as it is and queued in TODO.md; unifying it is a behavior
change to a shipped path and belongs in its own fix, where it can be
tested and documented on its own terms.

## Sketch

- **`cli/discover_cmd/window.py` moves to `cli/window.py`.** It already
  contains nothing discover-specific — an `isatty` gate and two
  `shutil.get_terminal_size` reads — and a second caller makes the
  location wrong. Pure move plus import updates in
  `discover_cmd/stages.py`, that package's `__init__` docstring, and
  two test modules — one of which
  (`tests/test_discover_window_degraded.py:102`) monkeypatches the
  module path as a **string literal**, which an import-rewrite sweep
  would miss. `_interactive` becomes public as `is_interactive`.
- **A new top-level `text_window.py`** holds the shared physical-line
  arithmetic: `wrapped_height` (moved from `discover_render.py`, two
  call sites) and `fit_by_cost`. `discover_paging.fit_rows` becomes an
  adapter over it. This is the extraction the first adjudication
  requires, and it gives the shared rule a name that does not say
  "discover" in a module the pull flow imports.
- **A new `cli/pull_exec/listing.py`** owns the rendering: group
  `info.files` by first path segment preserving hub order, build the
  roll-up lines, build the flat lines, and decide (given a budget and
  a line cost) whether the flat listing fits. Data in, lines out, no
  I/O — one pytest verifies grouping, sums, ordering, and the fit
  predicate without a terminal.
- **`prompt_for_selection` becomes the loop** over two frames, holding
  a window offset into the flat rows. It keeps its signature and return
  type, so `flow.py:95` is untouched.
- **`prompts.py` stays under the 300-line cap** by that split; it is 85
  lines today and the loop plus key parsing would push it toward the
  limit alone.
- Files touched, so the count is visible before `/plan`: `prompts.py`,
  new `listing.py`, `window.py` (moved), `discover_cmd/stages.py`
  (import), `docs/cli.md`, plus two or three test modules, `TODO.md`,
  and `CLAUDE.md`. That is above the five-file threshold in
  `CLAUDE.md`; this spec is the ask.

## Notes

- **Resolved at the plan round** (was an open question here): a repo
  whose flat listing fits gets no key line at all, not merely no `f`.
  See the fifth adjudication.
- **Verification owed live, not just in tests.** Every listing spec in
  this repo has been out-found by a real run — 0015's sizing was
  simulated in tests and the `screen` check is still owed, and the
  live-use round has beaten the review round on usability on this
  surface repeatedly. The Kimi repo above is the reproduction case: it
  should be walked end to end on a real terminal before this ships,
  including a narrow (80-column) window where the long shard names
  wrap.
