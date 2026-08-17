# 0020 — Planning Stop Recovery Command

**Status:** shipped (PR #36)
**Last updated:** 2026-08-17

## Goal

A pull that stops during *planning* names a way out and then withholds
the command that takes it. The live trigger (2026-08-16) was a
`discover 'deepseek v4 flash'` walk — search, tree, `0` to pull,
`1 = pick files`, patterns `*dspark*,*UD-Q4*,*UD-Q8*` — that ended here
and nowhere else:

```text
error [integrity]: gguf/docs/unsloth--DeepSeek-V4-Flash-0731-GGUF/README.md
is recorded with size 5651 but the hub reports size 5826 and publishes no
hash to compare; the archive is payload-immutable — re-run with
--refresh-docs to replace this documentation file
```

Brian's question was "re-run *what*?" `--refresh-docs` is a `pull`
flag. `discover` does not have it and cannot take it, so the instruction
is unfollowable from the surface that printed it: there is no command in
shell history to append it to (history holds `discover 'deepseek v4
flash'`), and the pull's actual shape — repo id, archive path, three
typed patterns — exists only in the scrollback the human would have to
retype by hand, correctly, including the fact that command-line
`--include` does not split on commas the way the interactive prompt
does.

The tool already knows how to do this. `compose_resume_hint`
(`cli/resume_hint.py`) composes exactly that command, shell-quoted and
control-character scrubbed, and `run_pull` holds every input it needs at
the moment of the stop — `patterns` is bound by the first statement
inside the `try` (`cli/pull_exec/flow.py:89`), so the interactively typed
selection is in scope in the `except`. The reason it does not print is
sequencing: the hint is captured in `capture_resume_hint`, wired to
`pull_model`'s `on_transfer_start` seam, and a planning stop raises
inside `prepare_pull` — before the first byte, therefore before that seam
ever fires. The hint is owed to a transfer; this failure happens one step
earlier.

This is the third instance of one rule the repo keeps re-learning:
**a dead end that teaches something must hand over the means to act on
it.** Spec 0007 gave interrupted transfers the continue command; spec
0013 appended the recovery command to an Ollama-shaped id error
(`ollama_shape_hint`, echoed to stderr right after the error, detection
in the error path only); spec 0018 made a no-match error sample paths
instead of walling. Planning-time stops never got the treatment, and
they are the ones most likely to fire at the end of an interactive walk,
where the human has the least to retype from.

Absorbed into the same change: the queued item at `TODO.md:404` (from
the 0014 review round, 2026-07-31) — planning errors name a path
relative to the model directory without naming the directory itself.

## Success criteria

- **A doc-refresh integrity stop ends with the command that resolves
  it.** After the existing error line, the tool prints one pasteable
  `pull` command replaying the pull's own shape — exact repo id,
  archive path resolved absolute, **one `--include` per typed pattern**,
  plus any `--role` / `--base-model` / `--hf-logging` in effect — with
  `--refresh-docs` appended. Pasting it and pressing enter is the whole
  recovery.
- **It works from `discover`.** The command names `pull`, never
  `discover`, and carries the shape assembled during the walk. This is
  the case that motivated the spec; a test drives the discover handoff
  end to end, not just `pull` directly.
- **The line goes to stderr, immediately after the error**, following
  the spec 0013 precedent (`ollama_shape_hint`). Stdout stays clean for
  a piped run.
- **The exit code does not move.** A doc-refresh stop stays fault domain
  `integrity`, exit 5. Nothing about the failure changes; only what the
  human is told.
- **It prints on every pull, not only interactively shaped ones.** This
  deliberately differs from spec 0007's hint, which is captured always
  but printed only when the shape was assembled interactively because a
  user-typed shape is already in shell history. Here, history is *not*
  enough: what the human lacks is the flag, not the shape. A pull typed
  in full still gets the line, with `--refresh-docs` added to it.
- **`--plan` gets the same line.** A dry run that hits the stop reports
  the recovery command as the real run would, so the rehearsal is honest
  about how to proceed.
- **Dispatch is by exception type, never by matching the message
  text.** The doc branch of `_immutability_stop` raises a typed carrier
  (a `PullIntegrityError` subclass, mirroring how spec 0013's
  `PullInvalidIdError` carries its own recovery path); the handler keys
  on the type. String-matching `--refresh-docs` in a message is the
  substring-satisfied guard this repo already shipped once and had to
  fix in spec 0017 — a test asserting on it passes with the code it
  names deleted.
- **A changed *weight* gets no recovery command, and `--refresh-docs`
  is never suggested for one.** The weight branch keeps its current
  wording ("replacing or adding the new content requires an explicit
  choice") and prints no command. Pinned by a test that fails if the
  weight path ever emits a command line, because the whole value of the
  doc line is that it is safe to paste without thinking.
- **A repo id that fails validation yields no line at all.**
  `compose_resume_hint` already returns `None` for an id that is not
  shaped like a repo id, and everything is scrubbed then shell-quoted;
  the recovery path inherits both properties rather than reimplementing
  them. No hint beats a booby-trapped one.
- **Planning stops name the model directory holding the conflicting
  record** (`TODO.md:404`), so the message is self-contained — the human
  can go look at the file named without first deriving where it lives.
  Applies to the immutability stops and the unrecorded-file reconcile
  stop (`pull_plan.py:103`).
- **A test drives the real error path**, asserting the composed line
  byte for byte for a multi-pattern interactive selection — the
  three-`--include` expansion is the specific thing a human retyping by
  hand gets wrong, so it is the specific thing pinned.

## Non-goals

- **No escape hatch for changed weights.** The archive stays
  payload-immutable (ADR 0001). No `--force`, no doc-style refresh for
  payload, no widening of what counts as a doc file.
- **The tool does not suggest a destructive command as a way out.**
  `remove` is not proposed in an error path, here or anywhere. An error
  message is exactly the moment a human is most inclined to paste
  without reading, and `remove` is the one verb that deletes archived
  weights.
- **Not an audit of every message in the tool.** Scope is the pull
  planning path: the stops raised from `pull_plan.py` and
  `pull_prepare.py` before any byte transfers. Other commands' errors
  are out of scope for this spec.
- **No change to exit codes or fault domains.** The four-code triage
  mapping from spec 0003 is untouched.
- **No new dependency, no new flag on `pull`.** The recovery command is
  composed from state the flow already holds.

## External references

None. Every value this spec touches is the tool's own — its flag names,
its exception types, its existing hint composer. No external-authority
constant is introduced, so nothing here inherits the provenance
requirement in `.claude/rules/python-code.md`.

## Notes

Three calls settled by the human before `/plan` (2026-08-16):

- **`discover` does not gain a `--refresh-docs` passthrough.** The
  recovery command is the whole fix. A flag on `discover` would have to
  be set before the search runs — before the human can possibly know a
  doc conflict exists — so it would be either guesswork or a habitual
  always-on that quietly makes doc replacement the default. The stop is
  the first moment the decision is real, and the command is what makes
  that moment actionable.
- **The every-weight decline (`pull_prepare.py:160` — "every-weight
  pull declined: narrow `--include` and re-run") is deferred, not
  fixed here.** It names a flag the same way and is reachable from the
  same discover walk, but the composed command would carry the
  *rejected* selection, so a paste-and-go line would be actively wrong:
  the whole point is that the selection needs narrowing first. It comes
  back only if the line can be made to say "narrow this" without
  becoming a footgun. Queued in TODO rather than built.
- **The reconcile stop (`pull_plan.py:103`) gets the model directory
  and nothing else.** It has no flag to name; whether it should point
  at `verify` is a separate call, not this spec's.

Settled at the review checkpoint (2026-08-17):

- **The stop's wording now says the flag is plan-wide.** The adversarial
  round built a repo where `README.md` *and* `LICENSE` changed upstream:
  the error named only the README, and pasting the recovery command
  replaced both — the archived license text under which those weights
  were obtained, gone. The stop raises on the first conflict it plans,
  so it cannot enumerate the rest without planning past its own refusal;
  what it can do is stop implying there is only one. "re-run with
  `--refresh-docs` to replace this documentation file" became "…which
  replaces every documentation file whose upstream content changed, not
  only this one", and the recovery line's lead-in moved from "the
  changed documentation file" to "every changed documentation file".
  No behavior change, and the larger fix — keeping the superseded card
  rather than overwriting it — stays queued in TODO rather than
  expanding this spec.
- **The line still prints for a fully typed pull**, as criterion 5 says.
  The counter-argument (the error above already names the flag, and the
  shape is in history) was weighed and declined: a history entry still
  has to be hand-edited to insert the flag correctly, and a rule that
  always fires is easier to trust than one that fires sometimes.
- **The `closeout-check.sh` SIGPIPE fix rides this branch** rather than
  its own, unlike spec 0019's deliberate one-idea discipline. It is a
  prerequisite, not a rider: the `## Shipped` section now exceeds the
  pipe buffer, so without the fix `/review-check` falsely refuses this
  branch and every future spec branch.

Implementation notes:

- `compose_resume_hint` hardcodes the lead-in `to continue this pull
  later`, which is wrong text for this context. Parameterize the lead-in
  or add a sibling composer over the same quoting path — do not
  duplicate the scrub-then-quote logic, which is load-bearing security
  behavior (spec 0007's review round).
- The handler in `run_pull` (`cli/pull_exec/flow.py:208`) already has
  `repo_id`, `path`, `patterns`, `select_all`, `roles`, `base_model`,
  and `hf_logging` in scope, and already appends a recovery line for one
  error type. The new line goes beside it.
- Size: small/medium — one exception subclass, one composer change, one
  handler branch, plus the model-directory string in three messages.
  Under the full loop, but not a multi-phase feature.
