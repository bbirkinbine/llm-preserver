# 0021 — Quit At Pull Prompts

**Status:** shipped
**Last updated:** 2026-09-03

## Goal

A `discover` walk asks four questions in a row. The first three each
advertise `q = quit`. The fourth silently reads `q` as a glob. The live
trigger (2026-09-03) was a `discover 'minimax h3'` walk that ended like
this:

```text
files to pull (comma-separated patterns, e.g. *Q4_K_M* or *.gguf,*mmproj*): q
error [user input]: no files in Comfy-Org/MiniMax-H3 match include
patterns ['q'] (docs always ride along but cannot be the whole pull);
adjust --include — available files: .gitattributes, …
```

The three prompts immediately above it — the search pick, the tree hop,
and `archive how?` — all end in `q = quit` and all quit at exit 0. The
file listing does not, on the path that run took: the repo has 29 files
and the terminal was tall enough to hold them (the tree frame rendered
51 rows in a single window), so the listing took the *fits* path at
`cli/pull_exec/prompts.py:176` — no footer, no key line, the generic
`*Q4_K_M*` example — and that path neither offers nor honors `q`. The
windowed path from spec 0018 does (`listing/frame.py:49`,
`prompts.py:248`).

This is not an oversight. It is spec 0018's **offered keys only** rule
holding literally, and `docs/cli.md:307` already documents the exact
outcome:

> **Keys are only keys where a key line is showing** — a listing that
> fits prints none, so a bare `q` there is a pattern matching a file
> named `q`, while on an overflowing listing it quits (exit 2).

The rule is right and stays. What is wrong is applying it to `q`. The
other reserved keys are frame-local — `m`, `b` and `s` mean something
only inside a windowed listing, and are taught only there. `q` is a
property of the *walk*: it is taught three times before this prompt, by
three different stages, and the human who has been told three times that
`q` gets them out has no reason to read the fourth prompt's silence as a
change of contract. Offering `q` on the fits frame keeps the rule true —
it is a key because it is offered — and the escape hatch for a repo
holding a file literally named `q` already exists and is documented:
keys match the whole answer before the comma split, so `q,` is the
pattern list `["q"]`.

The second half is what quit *does*. Where `q` works today it raises
`PullUserError`, so pressing the advertised quit key prints
`error [user input]: nothing pulled: quit at the file listing` and exits
2. The same is true of the two confirmations after it: answering `n` to
the size confirm is `pull declined: nothing downloaded` at exit 2, and
declining the every-weight confirm is exit 2. A human answering a
question the tool asked is not a fault, and `pull` is the only command
in the tool that says otherwise — `remove` documents a declined
confirmation as exit 0 "nothing removed" (`docs/cli.md:1215`), and
`discover`'s three quit prompts return cleanly. The tool's own stated
principle is that exit codes name the fault domain; there is no fault
here. So this spec adopts one rule for the whole pull flow: **a human
who declines or quits at a pull prompt gets a plain line saying nothing
was pulled, and exit 0.**

Absorbed into the same change: the queued item at `TODO.md:39` (found by
spec 0018's plan round, pre-existing) — EOF at the interactive
file-listing prompt escapes every handler and dies with click's bare
`Aborted!` at exit 1. It is the same prompt and the same question, and
it was deferred off 0018 precisely because it wanted its own test. The
answer it gets here is the isatty split `remove` already makes: an
*answered* decline, Ctrl-D included, is exit 0; a prompt that is
*unanswerable* because stdin is not interactive stays the scripted fault
it is today, exit 2 naming the bypass.

## Success criteria

- **A fitting listing offers `q` and quits on it.** An interactive
  listing that fits on screen prints a `q = quit` key line, and a bare
  `q` at its prompt quits instead of being read as a pattern. This is
  the live trigger, verbatim: the walk above ends with a quit, not a
  no-match wall.

- **A piped listing is byte-identical to today.** No key line, no quit:
  a pipe offers no keys, so `q` there remains the pattern `["q"]` and
  the existing no-match error stands. Spec 0018's "a pipe prints
  everything, flat" is untouched, and no scripted pull changes
  behavior. A test compares a piped run's output against the current
  bytes.

- **The new key line is charged before the frame is sized.** The fits
  check adds the `q = quit` line to its chrome, so a listing that fits
  only *without* the key line takes the windowed path rather than
  printing one physical row over budget. Following spec 0018's own
  lesson, the check is exercised across a range of terminal widths and
  heights, not at a single hardwired geometry — including the boundary
  where charging the line is what flips the decision.

- **`m`, `b` and `s` stay patterns on a fitting listing.** They are not
  offered there, because there is no page to advance to, none to go
  back to, and no summary to return to. Only `q` is added, and only
  because only `q` is offered.

- **Quitting the file listing exits 0.** Stdout carries one plain line
  (`nothing pulled: quit at the file listing`), with no `error [...]`
  prefix, no stderr output, and no `pulled … into …` final line. This
  holds on both the fits frame and every windowed frame that offers
  `q` — the windowed path moves from 2 to 0 with the rest.

- **Declining the size confirmation exits 0**, printing the same shape
  (`nothing pulled: size confirmation declined`).

- **Declining the every-weight confirmation exits 0**, printing the
  same shape and keeping its existing advice (narrow `--include` and
  re-run). These three — `prompts.py:248`/`:267`, `pull.py:244`,
  `pull_prepare.py:160` — are the complete set of human-answered
  declines in the pull flow; no other `PullUserError` site changes.

- **Ctrl-D at the interactive file-listing prompt is a decline**, not a
  crash: exit 0 with the quit line, replacing today's bare `Aborted!`
  at exit 1. This closes `TODO.md:39`.

- **Ctrl-C at a prompt declines the same way, and that is accepted
  rather than worked around** (review round, 2026-09-03). click folds
  `KeyboardInterrupt` into the same `Abort` it raises for EOF, so the
  two are indistinguishable at this seam without catching the signal
  ahead of the library. `remove` has treated an interrupted prompt as
  a decline since spec 0010, no bytes have moved at either prompt, and
  `run_pull`'s own `except KeyboardInterrupt` still owns the
  mid-transfer case at exit 130 — the distinction that matters is
  whether the transfer had started, not which key was pressed. A test
  drives the real conversion through typer's *vendored* click, since
  `typer.prompt` does not read the `click` package's module.

- **Ctrl-D at a confirmation on a terminal is a decline too.**
  `confirm_or_stop`'s `typer.Abort` branch splits on the same
  interactive verdict as the listing prompt: with an interactive stdin
  it becomes that prompt's decline, printing the line an answered `n`
  would print and exiting 0; with no interactive stdin it stays exit 2
  naming the bypass. Today it exits 2 on a terminal claiming
  `stdin is not interactive`, which is false on the surface it just
  printed to, and would leave Ctrl-D meaning two different things one
  prompt apart (adjudicated at the plan checkpoint, 2026-09-03).

- **An unanswerable prompt stays exit 2.** With stdin not interactive,
  reaching the file-listing prompt or either confirmation is still the
  scripted fault it is today: `error [user input]:` naming the bypass
  (`--include` / `--whole-repo` for the listing, `--yes` for the size
  confirm), exit 2. The interactive verdict is what separates the two,
  and it is taken from **stdin** — answerability is a question about
  who is typing, not about whether the frame has a wall (spec 0018's
  stdout verdict keeps its own job). A test drives both sides at each
  prompt: the same prompt, answered by a human and unanswerable by a
  script, must not share an exit code.

- **Dispatch is by exception type, never by matching message text**
  (spec 0020's rule), and a decline is not a `PullError` — the
  fault-domain mapper (`exit_for_pull_error`) must not be able to reach
  it, so no future fault domain can silently reclassify a decline.

- **`--yes` is unchanged.** It auto-accepts the size confirmation only,
  so it cannot produce a decline; the every-weight and listing prompts
  are unaffected by it.

- **A file literally named `q` is still archivable**, via `q,` at any
  frame. A test pins it, because the fits frame is where a one-character
  repo path is least improbable.

- **The docs move with the behavior.** `docs/cli.md` updates the "keys
  are only keys" paragraph (a fitting listing now prints one key line),
  gains an exit-0 row on the `pull` exit table naming the declined
  prompts alongside the existing "nothing new to pull", and states the
  answered-vs-unanswerable split on the exit-2 row.

- **Every assertion is differential.** Following spec 0020: a test that
  reads "exit 0" or "no error prefix" must fail with the feature
  deleted. Each criterion above is mutation-proved by removing the code
  that satisfies it and confirming the test goes red.

## Non-goals

- **No audit of exit codes outside the pull flow.** `verify`,
  `migrate`, `status`, `show` and `remove` are untouched; `remove` is
  the model being matched, not a thing being changed.

- **No new keys anywhere.** No type-to-filter, no match preview, no
  arrow-key highlight — all three remain queued from spec 0018. This
  spec adds one key to one frame and changes what three existing
  declines exit with.

- **No change to `discover`'s prompts.** They already quit at exit 0;
  this brings the fourth prompt in line with them, not the reverse.

- **No new flag.** Quit is a prompt affordance, not a command-line
  surface.

- **The pipe path gains nothing.** A non-interactive listing keeps
  exactly the output and exit codes it has today, including the
  no-match error for `q`.

- **Not a rewording of the no-match error.** Spec 0018 already made it
  sample ten paths and count the rest; it stays as it is for the
  patterns that genuinely match nothing.

- **Live-verified by hand on the trigger walk itself** (Brian,
  2026-09-03): `discover 'minimax h3'` → `4` → `0` → `1`, and the
  prompt that started this spec now offers `q = quit` and quits at
  exit 0. Ctrl-C, Ctrl-D and an `n` at the size confirmation were
  exercised on the same walk and all three declined cleanly. The
  earlier pty run (`gpustack/bge-m3-GGUF`) covered the same paths
  mechanically; this one covered the repo and the keystrokes that
  produced the original report.

## External references

None. Every value this spec touches is the tool's own — its key
letters, its prompt strings, its exception types, its exit codes. No
external-authority constant is introduced, so nothing here inherits the
provenance requirement in `.claude/rules/python-code.md`.

## Notes

- **Why the fits path was the one that broke.** The windowed path was
  built by spec 0018 with `q` in it from the start; the fits path is the
  frame that shipped before 0018 and was deliberately left untouched
  ("a listing that fits is untouched"). The cost of that decision was
  invisible until a walk happened to land on a repo whose listing fit —
  which needs both a tall terminal and a modest file count, and is why
  the gap survived a review round and a live round.

- **The decline line goes to stdout**, matching `remove`'s "nothing
  removed". Stderr was the alternative, on the grounds that nothing was
  accomplished; `remove`'s precedent decides it, and it keeps the
  stream split meaningful — a decline is not a diagnostic.

- **The every-weight decline is in the rule.** The adjudication named
  `q` and the size confirm explicitly; the every-weight confirm is the
  third human-answered decline in the same flow, and leaving it out
  would leave exactly one prompt still reporting a fault for an
  answered question.

- **Two scripted invocations move from 2 to 0**, both deliberate,
  both cases of a run that *answered* rather than one that could not.
  `echo n | pull …` is the obvious one. The review round found the
  second (2026-09-03): `echo q | pull …` with a **terminal on stdout**
  also moves 2 → 0, because the frame verdict reads stdout, so the key
  line printed and `q` was a key — and on `main` that was a no-match
  *user-input error*, a different class from a declined confirmation.
  An earlier draft of this note claimed `echo n |` was the only such
  change; it was wrong, and the correction is kept visible rather than
  edited away, because the "only one script is affected" claim is what
  made the contract change look cheap.

- **Exit 0 here does not mean the model is archived**, and that is the
  honest weakness of the change. The spec 0014 precedent is thinner
  than it first looks: 0014 moved a *complete re-pull* from 2 to 0,
  where exit 0 still means the model **is** in the archive. A decline
  exits 0 meaning the opposite — nothing was pulled and nothing will
  be. So `pull X && next-thing` now runs `next-thing` after a quit.
  The tool's stated principle decides it (exit codes name fault
  domains, and a human's answer is not a fault) and `remove` has
  behaved this way since spec 0010, but a script that needs "is it
  archived" must read the final line or `status`, never the exit code.
  Flagged by the review round; kept by decision.

- **Related, deliberately not taken:** the every-weight decline's
  composed-command hint (queued in spec 0020's non-goals) still carries
  the *rejected* selection, so it stays out. Changing what that prompt
  exits with does not change what it would print.
