# 0012 — Staging leftover detection

**Status:** draft
**Last updated:** 2026-07-19

## Goal

Surface abandoned downloads. A pull stages files under
`<archive>/.staging/<creator>/<model>/`, hashes and verifies them there,
moves them into `models/` as a single batch, writes the model record
**last**, then deletes staging (spec 0003/0005 invariants). An
interrupted pull — Ctrl-C, a crash, a dropped link — therefore leaves
partial bytes in `.staging/` and writes no record at all. The move is
all-or-nothing per pull, so a first-ever interrupted pull creates no
`models/<creator>/<model>/` directory whatsoever; only staging exists.

Nothing today surfaces this. `verify` (spec 0009) audits only the files
each record enumerates under `models/`, and `.staging/` is a *sibling*
of `models/` that neither `verify` nor `status` walks. So a
half-downloaded model reports nothing: an archive-wide `verify` returns
"all valid" while gigabytes of an unfinished pull sit forgotten in
staging. For a preservation tool, "I started a download and forgot to
finish it" is exactly the silent acquisition gap that should not be
invisible.

Detecting a leftover shares nothing with hashing — it is the presence of
a non-empty `.staging/<creator>/<model>/` directory. This spec adds that
detection as a hash-free scan and exposes it through `verify`, so finding
abandoned downloads never requires a hash run. Detection only: the report
is read-only, and the resolution verbs already exist (resume with `pull`,
which reuses staging; or discard with `remove`, which already clears
staging-only leftovers — spec 0010).

## Success criteria

- A standalone, hash-free scan primitive lives in `model_scan.py`
  (alongside `unrecorded_files`, so `verify` and any future caller
  cannot disagree about what "leftover" means). It enumerates
  `<archive>/.staging/<creator>/<model>/` directories that contain at
  least one regular file and returns, per leftover, the
  `<creator>/<model>` id, the on-disk byte total, and the file count. It
  loads no record, walks no `models/` tree, and hashes nothing —
  near-instant regardless of archive size. Empty stale directories
  (no regular file) are not leftovers. The byte total and file count
  cover **everything** under the leaf, huggingface's own
  `.cache/huggingface/` local-dir bookkeeping included (the `.metadata`
  sidecars and the in-progress `.incomplete` blob a `download` leaves in
  the staging dir). The goal is to surface *all* incidental staging
  space a `verify` cannot see so the human can decide what to do with
  it — not to classify payload versus hf-internal (which would depend on
  hf's cache layout, and would hide a single large file interrupted
  mid-download, whose only bytes live in `.cache/…/*.incomplete`).
  Confirmed 2026-07-19.
- `llm-preserver verify --staging` runs **only** that scan and skips the
  recorded-file audit entirely (no model walk, no hashing, no manifest
  refresh). It prints one line per leftover —
  `<creator>/<model>  X.X GB, N partial files` — sorted by id, or an
  explicit `no abandoned downloads in .staging/` when clean. This is the
  instant "did I forget a download?" check the spec exists to provide.
- A leftover is **informational**: it never trips exit 5 (reserved for
  drift of *recorded* data) and `verify --staging` exits 0 with
  leftovers listed. Same tier as verify's existing `unhashed` /
  `unrecorded` categories — a leftover is an incomplete acquisition the
  user chose to interrupt, not corruption of preserved data, and a cron
  job must not page forever because someone Ctrl-C'd a pull. The
  existing usage/archive exit codes still apply to `--staging` (1 = not
  an archive, an unreadable `.staging/`, or a malformed `--model` shape;
  2 = a well-formed `--model` that names no staging leftover; 130 =
  Ctrl-C). This mirrors verify's spec-0009 split — malformed shape is
  exit 1 via the shared `split_model_id`, unknown id is exit 2.
- Plain `verify` (full **and** `--quick`) emits a compact
  informational footer when leftovers exist — a one-line count pointing
  at `verify --staging` for the detail (e.g.
  `note: 2 abandoned downloads in .staging/ — run 'verify --staging'`).
  The recorded-file audit stays the focus and the exit code is
  unchanged, but a routine audit never silently hides an abandoned
  download — the whole failure mode is *forgetting*. (Recommended
  default; see Open questions.)
- `--staging` composes with `--model <creator>/<model>`: it scopes the
  staging scan to that one model's leftover directory, with the same id
  shape-validation the command already applies. Under `--staging` the
  relevant id namespace is the staging tree, not `models/`: an unknown
  `--model` lists the leftover ids present in `.staging/` (or reports
  none), rather than the `models/` inventory `verify --model` lists
  today.
- `--staging` is hash-free by construction, so `--quick` has nothing to
  act on when combined; `--staging` short-circuits before the audit and
  `--quick` is a documented no-op in that combination (not an error).
- Each leftover the report names is resolvable with commands that
  already exist, and `docs/cli.md` states both: resume the original
  `pull <creator>/<model> …` (staging is reused, so completed shards are
  not re-fetched — spec 0007), or discard it with
  `remove <creator>/<model>` (whole-model `remove` already clears a
  staging-only leftover that has no `models/` directory — spec 0010).
  The scan prints the id `remove` needs; this spec adds no deletion path
  of its own.
- Same path-safety posture as every other archive surface: a symlinked
  `.staging/` is refused (as `models/` is), a leftover directory reached
  through a symlink at any level (container, creator, or leaf) is
  skipped, and the byte total sums regular files only — symlinked dirs
  are never descended (matching `iter_model_dirs` / `remove`'s
  `reached_through_symlink`). Every leftover line runs through
  `clean_text(single_line=True)`, since `<creator>/<model>` derives from
  a hub-supplied id (control-character hygiene, same as verify's other
  output).
- `verify -h` continues to list the command's options, now including
  `--staging`; `docs/cli.md`'s verify section documents the flag, its
  informational exit semantics, and the resume/discard resolution. The
  README loop mention is updated only if the surface warrants it.

## Non-goals

- **Repair or deletion.** Detection only. The report names abandoned
  downloads; resuming (`pull`) or discarding (`remove`) is the user's
  call, through commands that already exist. This spec writes nothing to
  the archive — not even the `manifest-sha256.txt` sidecar a full
  `verify` refreshes (`--staging` never touches `models/`).
- **A new exit code for leftovers.** Considered and declined: leftovers
  stay informational (exit unchanged). If CI/cron ever needs to fail on
  a forgotten download, that is a follow-up, not this spec.
- **Distinguishing an in-flight pull from an abandoned one.** A pull
  running concurrently also has content in `.staging/`; without a lock
  file the scan cannot tell a live transfer from a forgotten one. Verify
  is expected to run when no pull is active; a concurrent pull's staging
  is transient. Documented as a known limitation, not solved here.
- **Reconstructing the original pull command.** The scan knows the
  `<creator>/<model>` id but not the `--include` patterns of the
  interrupted pull, so it cannot reprint the exact resume command (spec
  0007's hint is printed at interrupt time from live state, not
  reconstructed after the fact). The report points at `pull` / `remove`
  generically.
- **A separate subcommand or a `status` surface.** Detection rides
  `verify` (where the blind spot was noticed) as a flag, plus the
  footer. Adding it to `status` is a possible later convenience, not
  part of this spec.

## Open questions

- **Footer on plain `verify`.** The draft has plain `verify` (full and
  `--quick`) print a one-line leftover note in addition to the
  `--staging` deep view. Recommended, because the failure mode is
  forgetting and a silent routine audit would reintroduce the blind
  spot. Reject it if the leftover report should live *only* behind
  `--staging`.

## External references

None — original. Staging layout (`.staging/<creator>/<model>/`) is the
tool's own convention, established by specs 0003/0005 and already
consumed by `pull_preflight.already_staged_bytes` and `remove`. No
outside authority governs any value in this spec.

## Sketch

- Add `staging_leftovers(root) -> list[StagingLeftover]` to
  `model_scan.py` — a `StagingLeftover` dataclass (`model_id`, `path`,
  `total_bytes`, `file_count`). Walk `root / .staging` with the same
  `_real_subdirs`-style symlink refusals as `iter_model_dirs`; sum
  regular-file sizes per `<creator>/<model>` dir; skip empties.
- Add `--staging` to the `verify` command in `cli/verify_cmd.py`. When
  set, short-circuit before `verify_archive`: run the scan, render the
  lines (or "none"), honor `--model` as a staging-scoped filter, exit 0.
- When not set, call the scan once after the audit summary and print the
  footer line if it is non-empty.
- The planner expands file-by-file; the id-namespace handling for an
  unknown `--model` under `--staging` (list staging ids, not `models/`
  ids) is the one non-obvious wiring point.
