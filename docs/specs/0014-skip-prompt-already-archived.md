# 0014 — Skip Prompt Already Archived

**Status:** shipped (PR #23)
**Last updated:** 2026-07-31

## Goal

Re-running a `pull` whose selected files are all already archived
currently walks the user through the grouping confirmation ("declares
no base_model; archive it as canonical model X?") before revealing —
via the download plan — that there is nothing to do. The confirm runs
first because its answer *decides* the archive directory
(`resolve_model_id` in `pull_prepare.py`), and the already-archived
check depends on that directory. But when the default home the prompt
offers is the repo id the user typed (no `base_model`, the
hf-snapshot lineage case, or an explicit `--model`), the flow can
tentatively resolve it, load its record, and run the same
`plan_downloads` before asking anything. When the plan finds nothing
to download and nothing to adopt, no answer changes the outcome —
**y** reaches today's "nothing to pull" no-op and **N** aborts, both
no-ops — so the pull skips the confirmation entirely and reports that
the selection is already archived. Any plan with work to do prompts
exactly as today.

**Scope restriction (review-round adjudication, 2026-07-31):** a
*hub-derived* home — the declared `base_model` a GGUF/MLX conversion
would group under — always confirms **before** the home names any
directory, exactly today's order. The spec 0006 invariant ("hub
metadata must never name an archive directory without a human yes")
is upheld: a review PoC showed a hostile `base_model` plus a
name+size-matched hashless file could otherwise steer a silent exit-0
"already archived" verdict at another model's directory for bytes
never verified identical. The prompt skip therefore applies only to
homes the user chose (the typed repo id, or `--model`).

## Success criteria

- A `pull` with `--include` patterns whose every selected file is
  already archived under the default home prints a clear
  already-archived line naming the model directory and exits 0
  without asking the grouping confirmation (or any other prompt).
  The per-file "already archived: ..." INFO lines still print before
  the summary line, matching today's output; only the prompt
  disappears (confirmed 2026-07-31).
- A re-pull of the same repo where the selection includes at least
  one file not yet archived (e.g. a new quant) prompts and proceeds
  exactly as today — the check is file-level (the plan), never
  repo-level.
- `--refresh-docs` with changed upstream docs counts as work to do:
  it still prompts and proceeds as today.
- `--model` behavior is unchanged (it already skips the grouping
  prompt); the early exit also applies there — a fully-archived
  selection under the `--model` home reports already-archived and
  exits 0 instead of printing "nothing to pull" after the fact —
  the message is unified across both paths (confirmed 2026-07-31).
- `--plan` mode is unchanged in spirit: it never prompts today
  (confirmations become would-ask lines) and continues to render the
  full plan report, including for a fully-archived selection. The
  would-ask grouping line disappears exactly when the real pull would
  not ask (non-hub-derived home, nothing to do) and stays when it
  would (hub-derived home) — confirmed at the review round,
  2026-07-31.
- A hub-derived home (declared `base_model`) asks the grouping
  confirmation before the home names any directory — even when the
  selection turns out fully archived (the 0006 invariant, upheld
  2026-07-31).
- The final CLI line on the nothing-to-do path says what happened
  ("`<repo>` is already archived in `<dir>`; nothing new to pull")
  instead of the pull-success wording — a run that moved no bytes and
  wrote no record must not read as one that did (review-round
  adjudication, 2026-07-31).
- The every-weight confirmation and the size confirmation still fire
  in their current situations on pulls that have work to do; internal
  reordering (plan computed before the grouping confirm) must not
  change which prompts a real pull asks or their user-visible order.
- Interactive listing mode (no `--include`, no `--whole-repo`) still
  presents the file listing first; the early exit applies after
  selection if the chosen files are all archived.
- All existing tests pass; new tests cover: fully-archived re-pull
  (no prompt, exit 0), partial overlap (prompts), `--plan` on a
  fully-archived selection, and `--refresh-docs` with doc changes.

## Non-goals

- No repo-level "already have this repo" shortcut that skips the
  metadata call — the check requires the tree and the plan, and spec
  0003's one-metadata-call budget is unchanged.
- No change to what "already archived" means: `plan_downloads` stays
  the single authority (hash match, or name+size when the hub
  publishes no hash).
- No suppression of advisories or the grouping prompt on pulls that
  have any work to do; this spec touches only the nothing-to-do path.
- No new flags.

## Notes

- The reorder lives in `prepare_pull` (`pull_prepare.py`): compute
  the *candidate* default home without confirming, load the record,
  plan downloads; if `plan.to_download` is empty, surface that to the
  caller so the flow can exit before any confirmation; otherwise ask
  the grouping confirm (and the rest) as today. The every-weight
  confirm may move after the nothing-to-do check in code, but on any
  pull with work to do the user-visible prompt order stays
  grouping → every-weight → size (confirmed 2026-07-31).
- A declined grouping today exits with an error ("grouping under X
  declined"); on the nothing-to-do path that outcome disappears along
  with the prompt — net effect identical (nothing happens), exit code
  changes from the decline-error to 0. This is the deliberate trade.
- Live-use trigger (Brian, 2026-07-31): re-pulling
  `gpustack/bge-m3-GGUF --include bge-m3-FP16.gguf` asked the
  canonical-model question, then reported everything already
  archived. That repo declares no `base_model`, so it sits squarely
  in the skip's post-adjudication scope.
- Rider from the security review (2026-07-31): the advisory-only
  `adapter_config.json` fetch now requires a hub-declared size — an
  undeclared size is untrusted, not unlimited, since the fetch can
  run with zero prompts on the no-op path. Pre-existing mechanism,
  newly prompt-free reach.
- Queued (review round, 2026-07-31): planning hard stops (e.g. a
  changed-weight integrity error) now fire before any prompt and name
  the file but not the model directory — a small follow-up should add
  the directory to those messages (TODO.md).
