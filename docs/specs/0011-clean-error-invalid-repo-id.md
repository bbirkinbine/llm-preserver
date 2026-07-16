# 0011 — Clean error on an invalid repo id

**Status:** shipped
**Last updated:** 2026-07-15

## Goal

A `pull` (or any hub-facing command) given something that is not a valid
Hugging Face repo id currently crashes with a full rich Traceback instead
of a clean one-line error. The trigger in live use (2026-07-15):
`llm-preserver pull 'qwen3-vl:30b-a3b-instruct'` — an Ollama `name:tag`
reference pasted where the tool wants a hub `org/name` id. The `:` fails
`huggingface_hub`'s `validate_repo_id`, which raises `HFValidationError`.
That class subclasses `ValueError`, and `ValueError` is not in the hub
seam's `MAPPED_EXCEPTIONS` tuple (`HfHubHTTPError`,
`OfflineModeIsEnabled`, `httpx.HTTPError`, `OSError`), so it escapes the
fault-domain mapping in `hub/errors.map_hub_exception` and propagates
unmapped — Typer prints the stack. An invalid repo id is squarely a
user-input fault: it should print one clean line and exit 2, the same as
every other bad-input case (`--whole-repo` + `--include`, a bad archive
path, an unknown `--model`), and it should point the user at the recovery
path (`discover`).

## Success criteria

- `llm-preserver pull '<not-a-repo-id>'` (e.g. `qwen3-vl:30b-a3b-instruct`,
  or any string the hub's `validate_repo_id` rejects) prints **no
  Traceback**. It prints a single-line error to stderr and exits **2**
  (the user-input fault domain, consistent with `PullUserError` →
  exit 2 in `pull_exec/plumbing.PULL_FAULT_DOMAINS`).
- The message names the offending input and states the expected shape,
  e.g. `error [user input]: 'qwen3-vl:30b-a3b-instruct' is not a valid
  Hugging Face repo id (expected '<org>/<name>')`. <!-- assumption:
  exact wording is the implementer's choice; the invariant is one line,
  names the bad value, states the org/name shape -->
- The message points at the recovery path: search the hub for the model
  by name with `llm-preserver discover <query>`. <!-- the discover
  command is the deterministic way to turn a fuzzy name into an exact
  hub id -->
- **No false negatives.** The change must not reject any repo id the hub
  would accept. Valid single-component canonical ids (`gpt2`), namespaced
  ids (`Qwen/Qwen3-VL-30B-A3B-Instruct`), and ids containing `.`, `-`,
  `_` all still reach the network unchanged. This is the argument for
  deferring the verdict to the hub's own `validate_repo_id` (map its
  `HFValidationError`) rather than reimplementing the regex at the CLI —
  a hand-rolled pre-flight check risks diverging from the hub's rules
  and blocking a legitimate pull. A test pins at least one currently-valid
  id that must still pass through.
- The fix covers **every** command whose argument becomes a hub request
  parameter, not just `pull` — whatever the chosen seam, a bad hub id
  anywhere yields the same clean exit-2 error, never a Traceback. (In
  practice `pull`'s `repo_id` argument is the only user-typed hub id
  today; `discover`'s navigation ids come pre-validated from the hub via
  `looks_like_repo_id`. The mapping-level fix covers all of them by
  construction and is future-proof for any new hub-facing entry point.)
- `docs/cli.md` documents the exit-2 behavior for an invalid repo id
  under `pull` (a one-liner in the errors/exit-codes section is enough).

## Non-goals

- No fuzzy matching, autocorrect, or "did you mean" repo-id suggestion
  beyond pointing at `discover` — the tool never guesses an id the human
  did not type (0000 design stance: no tool judgment; every pull targets
  an exact id the human chose).
- **No Ollama-shape detection in the message** (deferred, 2026-07-15):
  the error need not recognize a `name:tag` input as an Ollama reference.
  The generic "not a valid Hugging Face repo id (expected '<org>/<name>')"
  message plus the `discover` pointer is enough for now; a shape-specific
  hint can be a later refinement if the generic message proves confusing.
- No Ollama-registry integration or `name:tag` → hub-id translation.
- No change to the fault-domain taxonomy or exit-code meanings — this
  reuses `PullUserError` / exit 2 as-is.
- No new validation of `<creator>/<model>` arguments on `show` / `verify`
  / `remove` — those already validate via `cli/model_errors.split_model_id`
  and are out of scope.

## External references

`huggingface_hub` `validate_repo_id` / `HFValidationError` — the
authority for what strings are valid repo ids and the exception raised
for invalid ones. Verified against the installed `huggingface_hub`
1.23.0 in this session (2026-07-15): `HFValidationError.__mro__` is
`(HFValidationError, ValueError, Exception, ...)`, confirming it is not
caught by the existing `MAPPED_EXCEPTIONS` tuple; the validator lives in
`huggingface_hub/utils/_validators.py` (`REPO_ID_REGEX`, 96-char limit,
no leading/trailing `-`/`.`). The hub client (`hub/client.py`) already
pins its API facts against this same 1.23.0 install (Apache-2.0) — see
spec 0003 → External references. No new outside source; this spec reuses
that provenance. Implementation must defer the validity verdict to the
installed library rather than reconstructing the regex from memory.

## Sketch

Single seam — the hub-exception mapping, which localizes the fix and
covers every client call by construction:

- Add `HFValidationError` handling to `hub/errors.py`: include it in
  `MAPPED_EXCEPTIONS` (so `hub/client.py`'s `except MAPPED_EXCEPTIONS`
  catches it), and add a branch to `map_hub_exception` that returns a
  `PullUserError` carrying the clean message.
- The CLI boundary already turns `PullUserError` into exit 2 with a
  clean stderr line (`pull_exec/flow.py` `except PullError` →
  `exit_for_pull_error`), so no new CLI plumbing is needed once the
  mapping is in place.

Deferred (2026-07-15): a fail-fast pre-flight check in `pull_cmd.py`
using `looks_like_repo_id` is intentionally **out of scope** — the
mapping backstop is the whole fix for now. Deferring it also sidesteps
the false-negative risk of a second, hand-rolled validator diverging
from the hub's rules.

Expected touch: `hub/errors.py`, tests (a unit test on the mapping/
message helper plus a CLI test asserting exit 2 and no Traceback via the
Typer runner), `docs/cli.md`. ~3 files — Small tier; `/test-first`
required, `/plan` skipped (the source change is localized to one
module).

## Implementation Notes

- Shipped in PR #15. Fix is exactly the single-seam sketch: one branch
  in `map_hub_exception` plus `HFValidationError` added to
  `MAPPED_EXCEPTIONS`; the CLI boundary needed no change (the existing
  `PullUserError` → exit-2 path carried it).
- The CLI end-to-end test drives the **real** `HubClient`, not the
  `FakeHubClient` seam — the fix lives inside the client's
  `except MAPPED_EXCEPTIONS → map_hub_exception` path, and a fake
  (raising canned `Pull*Error`s) would bypass the code under test. It
  stays hermetic because `validate_repo_id` rejects the id locally,
  before any HTTP request; `HF_HUB_OFFLINE=1` is set as a belt.
- Adding the ~31-line test pushed `tests/test_cli_pull.py` over the
  300-line cap, so the error / fault-domain / output-hygiene tests were
  split into `tests/test_cli_pull_errors.py` (following the existing
  `test_cli_pull_*` sibling convention; helpers are duplicated per file
  as the other siblings do). Not in the original sketch's touch list —
  a review finding, resolved on the branch.
- Both independent reviewers returned "ship". The one security item
  (bidi / zero-width chars surviving `clean_text`) is pre-existing and
  already queued from spec 0007; this change funnels through the same
  scrubber, so it is covered when that hardening lands.
