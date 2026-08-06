# 0015 — Discover paging windows and stable pick numbers

**Status:** shipping
**Last updated:** 2026-08-06
**Depends on:** 0006

## Goal

`discover`'s two listings page by *accumulating and reprinting*: every
`m` appends the new rows to everything fetched so far and re-renders
the whole list from the top. Two problems follow, one cosmetic and one
not.

The cosmetic one is scroll. The tree stage fetches one `PAGE_SIZE`
page from each of the four relations before rendering anything, so the
**first** tree frame is up to 80 rows — measured at 89 non-blank lines
— and one `m` doubles it to 169. That overruns a terminal, and it
overruns the default scrollback buffer of a plain `screen` window, so
the top of the frame is not merely off-screen but unrecoverable
(live-use report, 2026-08-06). The frame-separator rule added in 0006
assumes scrollback exists; when it doesn't, the rule marks a boundary
you cannot scroll to.

The one that matters is that **tree pick numbers change meaning after
`m`**. `_tree_stage` keeps children in a flat list that it re-sorts by
relation on every loop, so a newly fetched quantized page is inserted
*ahead of* finetune rows that were already numbered on screen. Probe
measurement (four relations, 40 children each): after a single `m`,
**60 of 80 numbers pointed at a different repo** — pick 21 was
`f/child-00` before and `q/child-20` after. The failure is silent: read
a number, press `m` to check for something better, type the number you
remembered, and you drill into a repo you never looked at. This is the
same hazard that got `0` pinned as the stable pull key on 2026-07-13;
that fix covered the pull key alone and every child row still carries
it. The existing regression test cannot catch it — it configures a
single relation, where the re-sort is a no-op.

This spec replaces accumulate-and-reprint with an **append-only row
sequence rendered one terminal-sized window at a time**, with pick
numbers assigned at first display and never reused, and a `b` pick to
step back through windows already shown.

## Success criteria

- **Numbers are permanent.** For the whole life of one tree stage, a
  number that has been displayed always resolves to the same repo.
  Verified by a regression test with at least two relations each
  holding more than `PAGE_SIZE` children — the shape the current
  single-relation test cannot catch — asserting that picking a number
  read before `m` navigates into the repo that number named.
- **No frame exceeds one screen.** With a TTY, the rows rendered per
  frame are sized from the terminal height less fixed chrome (header,
  section labels, the `0` line, footer, prompt), with a floor so a
  very short terminal still shows a usable window. Sizing counts
  *physical* lines: a row wider than the terminal is charged the lines
  it wraps to, and so is the chrome. Without this the criterion is
  false in the common case — real hub ids render 90-100 characters and
  wrap at 80 columns, and a frame measured 39-45 physical rows against
  a 24-line terminal before the review round caught it (2026-08-06).
  Without a TTY the window is a fixed 20-*line* budget with no
  wrap adjustment, so piped output stays byte-identical across runs
  and machines — the 0009 precedent (TTY-only progress rendering,
  non-TTY output unchanged).
- **`m` never hands back a runt frame.** The buffer is topped up when
  what remains cannot fill a window, so a hub page that does not
  divide evenly into the window does not leave its tail as a one-row
  frame under five lines of chrome (measured 19, 1, 19, 1 at 80x24
  before the fix). Fetch *granularity* is unchanged — still one page
  per relation, all four advanced together; only the timing moves.
- **`m` prints only rows not yet seen.** No frame reprints a row that
  a previous frame already showed. A `discover` session that pages
  four times prints each row exactly once.
- **`b` steps back** one window through rows already fetched, with no
  network call, and is offered only when an earlier window exists.
  It exists because windowing removes what scrollback used to
  provide; without it the tool would be less usable on the terminals
  this spec is written for.
- **No frame containing nothing new is ever reprinted.** Today an
  exact multiple of `PAGE_SIZE` reprints the entire frame having
  fetched zero rows (observed: a 168-line frame with no new entries).
  With the top-up above, the end is usually known while the last
  window is being built, so `m` is withdrawn before it can be pressed;
  where a fetch can still come back dry mid-listing (a terminal tall
  enough that one hub page cannot fill one window), `m` answers
  `no further rows on the hub` in one line and re-prompts.
- **Every frame is self-sufficient.** Each frame names the repo whose
  tree is shown, carries the `0. pull this repo (<id>)` line, and ends
  in the prompt — so a user who cannot scroll back never has to. The
  ancestry ladder (bounded by `MAX_PARENT_HOPS`) reprints with each
  tree frame; it is the navigation spine and its numbers must stay
  reachable.
- **Search pages the same way.** Its frames grow too (22 lines, then
  ~42 after one `m`); it gets the same window treatment and the same
  `b`.
- **Hub fetch granularity is unchanged.** `PAGE_SIZE` stays 20 per
  relation and a fetch still advances all four relation pagers
  together — the 0006 reachability adjudication ("a base with hundreds
  of quants must not make its finetunes unreachable") is untouched.
  Fetching and displaying become separate concerns: one fetch feeds
  several windows.
- **`0` and `q` keep working from every frame**, and the invalid-pick
  refusal after `_MAX_INVALID_PICKS` is unchanged.
- The determinism test (`test_identical_plan_sessions_produce_identical_output`)
  still passes: same fake hub, same picks, byte-identical output.

## Non-goals

- **No TUI.** No `textual`, no `prompt_toolkit`, no new dependency, no
  alternate screen buffer, no arrow keys. Output stays plain
  `typer.echo` lines a pipe can read. The full-screen interactive
  listing stays queued in TODO.md as its own future spec; this spec is
  what makes the numbered-pick model bearable in the meantime.
- **No change to what a row says.** Hub facts, hub order, no ranking,
  no tool judgment (0006 and 0000 invariants).
- **No change to pull's file listing**, which has a related
  long-scroll problem. Same queued TUI item; out of scope here.
- **No jump-to-window pick** (`p3`, `g20`, …). `m` and `b` only.
- **No user-facing page-size flag.** The window follows the terminal;
  a flag is a settings surface this spec doesn't need to justify yet.

## External references

**None — original.** Every value here is the tool's own UX: the
`PAGE_SIZE` constant, the window sizing, and the pick alphabet are
defined by this repo, not by an outside authority. The hub API facts
this listing consumes (`downloads`, `last_modified`, `gated`, the
`baseModels` expand) were pinned in spec 0006 and are unchanged by
this spec. Terminal height comes from stdlib `shutil.get_terminal_size`,
which is a library call, not a cited external table.

## User-visible strings

Pinned here because `docs/cli.md` and the CLI tests must agree with the
renderer verbatim, and three separate work streams wrote against them:

- Footer: `showing {first}-{last} of {highest}`, then ` — more (m)`
  when rows remain after this window (buffered or still fetchable),
  then ` · back (b)` when an earlier window exists. `{highest}` is the
  largest number **handed out** so far — every number it counts is
  typeable — so it grows as you page and stays put when you step back.
  It is not a total; the hub publishes none.
- Prompt key hints, offered keys only, quit always last:
  `m = more, b = back a page, q = quit`. `b` spells out "a page"
  because the tree stage already means something by going back: the
  `your path:` trail pops when you hop to a repo you came from.
- Invalid pick: `not a listed pick — enter a listed number or one of
  m/b/q` (offered keys only); with a single key it reads `... or q`.
- End of the listing: `m` is withdrawn from the footer and the prompt
  once nothing remains. When a window was already open and the rows
  then ran out, `no further rows on the hub`, one line, then the
  prompt again — no reprinted frame.
- A window opening mid-section labels it `{relation} versions
  (continued):`; a later batch's own rows use the plain
  `{relation} versions:`.
- Everything else — the search and tree headers, the ancestry ladder,
  the `your path:` breadcrumb, the `0. pull this repo (...)` line, the
  frame-separator rule — is unchanged from 0006.

## Adjudications (plan round, 2026-08-06)

The planner surfaced four points the spec left implicit. Decided
rather than deferred, so the implementation has one reading:

- **Scrolled-off numbers stay pickable.** The pick set is every row
  displayed *so far* (the window high-water mark), not the visible
  slice. The stability criterion above is only meaningful if a number
  read two windows ago still works when typed — otherwise "numbers
  never change meaning" would be true but useless. Rows fetched into
  the buffer but never yet displayed are **not** pickable: the human
  cannot have read them.
- **Window floor is 5 rows** (`MIN_WINDOW_ROWS`). A terminal too short
  for the chrome plus five rows gets five rows and overflows; a floor
  of zero would make `m` unable to advance.
- **Deep ancestry can still overflow, and that is accepted.** The
  ladder reprints every frame (it is the navigation spine) and is
  bounded by `MAX_PARENT_HOPS = 10`. On a very short terminal, ladder
  plus chrome plus the floor can exceed the height. Dropping the
  ladder to fit would cost more than it saves, and the case is rare —
  real lineage chains are one to three hops.
- **`shutil.get_terminal_size` is read only after the `isatty` gate.**
  It consults `LINES`/`COLUMNS` *before* the OS, and this was
  confirmed live (2026-08-06): `LINES=99` changes its answer even when
  stdout is a pipe. Reading it first would let an exported `LINES` on
  a CI runner or a developer's shell change piped output and break the
  determinism criterion. The gate order is load-bearing, not stylistic.

## Adjudications (review round, 2026-08-06)

Three reviewers (independent, adversarial, security) converged on the
same defects; two were measured identically by two of them. All were
fixed on this branch rather than deferred, because each contradicted a
criterion this spec already claimed.

- **The footer counted rows fetched, not numbers handed out**, while
  the pick set was bounded by what had been displayed. A four-relation
  tree printed `showing 1-19 of 80` and then refused `60`. Fixed to
  `pinned_count + high_water`, so the footer and the prompt agree by
  construction.
- **Windows were sized in logical lines**, so wrapping broke the
  headline criterion (39-45 physical rows against a 24-line terminal).
  `fit_rows` now charges each row its wrapped height and
  `tree_chrome_lines` does the same for the chrome — including the
  `your path:` breadcrumb, which nothing bounds the way
  `MAX_PARENT_HOPS` bounds the ladder (a six-hop trail measured 444
  characters, charged as one line).
- **Fetching only when the buffer emptied left runt frames** — 19, 1,
  19, 1 rows at 80x24 — because a 20-row hub page does not divide into
  a 19-line window. The buffer is now topped up when the remainder
  cannot fill a window.
- **`resolve_window_size` crashed on a detached or closed stdout**
  (`AttributeError` / `ValueError` under a rich traceback), the
  0011/0012 class again: it ran before any output, so what had been a
  silent no-output run became a crash. It now degrades to the piped
  budget.
- **`b = back` was ambiguous** in a stage whose whole model is a
  navigation trail; the hint now reads `b = back a page`.

## Sketch

The state model changes from "a growing list that is re-sorted and
reprinted" to "an append-only display sequence plus a window offset".

- **Append-only sequence.** Each fetch appends one *batch* — the new
  page from each relation, in `_RELATION_ORDER` — to the end of the
  sequence. Nothing is ever re-sorted, so nothing renumbers. A pick
  number is the row's index in this sequence, assigned once. The
  visible consequence is that a relation's section label reappears in
  a later batch (a second page of quantized renders under its own
  `quantized versions:` heading), which is an honest description of
  what was fetched.
- **Windows.** The flow holds an offset into the sequence. `m`
  advances one window, fetching a new batch only when the offset would
  run past what is buffered; `b` moves it back, never fetching. The
  renderer takes a slice plus the fixed chrome and returns lines.
- **Sizing.** One helper resolves the window size: terminal height
  less chrome when `stdout` is a TTY, a fixed 20 otherwise, with a
  floor. Keeping it in one function keeps the non-TTY determinism
  guarantee checkable in one place.
- **Deletions.** The re-sort at `flow.py:131` and the
  `relation_rank` map go away — they exist only to make accumulated
  reprints group correctly, which is the behavior being removed.
- `parse_pick` gains `b` alongside `m`/`q`/numbers, gated on a
  `back_available` flag the way `m` is gated on `more_available`.

`docs/cli.md`'s `discover` section documents `b`, the one-window-at-a-
time behavior, and that numbers never change meaning.
