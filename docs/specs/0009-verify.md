# 0009 — Verify

**Status:** shipped
**Last updated:** 2026-07-13

## Goal

Audit the archive against its records, BagIt-style: report whether each
model is *complete* (every file its record lists exists on disk) and
*valid* (complete, and every file re-hashes to its recorded SHA256).
The archive now holds real Tier-1 content, and nothing today can detect
out-of-band deletion (the tool itself never deletes), bitrot, or
tampering — pull's skip path deliberately checks only per-file
existence (spec 0003), and `status` never stats payload files at all.
`verify` is the whole-archive drift detector the 0000 roadmap reserves
for exactly this: a read-only report; repair actions are the user's
call.

Vocabulary and layout are already decided (ADR 0001,
`docs/data-structures.md`): the record enumerates *expected* files,
which is what makes a partially rsynced or crash-interrupted model
directory detectable offline. This spec implements the check and the
per-model `manifest-sha256.txt` fixity sidecar reserved there.

## Success criteria

- `llm-preserver verify <archive-path>` walks every model directory
  (same gate-walk-degrade path as `status`) and prints a per-model
  result: **valid**, **incomplete** (recorded files missing — each
  named), or **invalid** (all files present but at least one hash or
  size mismatch — each named, with expected vs actual). A summary
  line gives archive-wide totals.
- The archive path resolves exactly like every other command: the
  positional argument, falling back to `$LLM_PRESERVER_ARCHIVE` when
  omitted (spec 0003's shared `ArchivePath` plumbing). A bare
  `llm-preserver verify` with the env var set audits the whole
  archive — pinned by test, since verify is the command most likely
  to run from cron where the env var is the natural configuration.
- `llm-preserver verify --model <creator>/<model>` scopes the audit
  to one model (same id validation as `show`) — a full hash run
  costs hours per terabyte over a NAS link, so "did that one model
  land intact?" must not require an archive-wide pass (confirmed
  2026-07-13). Scoping is an option, not a second positional:
  verify's model scope is optional (unlike `show`'s required id), and
  an optional model positional plus the env-var-optional path
  positional cannot be parsed unambiguously (`verify ~/archive` with
  the env var set would bind the path to the model slot).
- A `--model` value that matches no model directory
  errors *and prints the archive's model ids* (the same inventory
  `status` walks), so a typo or a half-remembered name self-corrects
  without a separate `status` round-trip (live-use ergonomics,
  2026-07-13). Archives are tens of models, not thousands — the list
  is printable.
- `verify --quick` checks existence and size only — no hashing — for
  both whole-archive and single-model scope: a seconds-long
  pre-backup sanity pass that catches deletion and truncation but not
  bitrot. The report states that hashes were not checked, models are
  reported **complete**/**incomplete** (never **valid**), and no
  `manifest-sha256.txt` is written or refreshed from a quick run
  (promoted to v1, 2026-07-13).
- A file whose recorded `sha256` is `null` (schema allows it) is
  checked for existence and size only, and reported in a distinct
  **unhashed** category — never counted as valid-by-default nor as a
  mismatch.
- Files on disk that no record lists are reported as **unrecorded**
  (informational — full BagIt completeness runs both directions;
  out-of-band additions are drift too). Generated files the tool
  itself owns (`model-record.json`, `MODEL-RECORD.md`,
  `manifest-sha256.txt`) are exempt. (Confirmed in scope 2026-07-13 —
  cheap, no hashing, catches half-finished manual copies.)
- Exit codes distinguish outcomes so a cron/scheduled run needs no
  output parsing (shape from the plan's fault-domain table, adjudicated
  2026-07-13): 0 = every checked model valid (full) / complete
  (`--quick`); 1 = not an archive / malformed `--model` syntax; 2 =
  `--model` matches no archived model (after printing the model ids);
  5 = drift — any model incomplete, invalid, no-record,
  record-unreadable, or any per-file I/O error; 130 = Ctrl-C.
  **Unhashed and unrecorded files report but do not trip exit 5**
  (adjudicated 2026-07-13): drift means something *changed* on disk,
  not "the record was never rich" — `status` already flags missing
  checksums, and a cron job must not page forever on a hashless
  cache-import or a hand-dropped extra file. Review-round refinements
  (adjudicated 2026-07-13): exit 2 is the broader user-input domain —
  the CLI framework's own usage errors (missing path with no env var,
  unknown flags) also exit 2, matching pull's table, so cron treats 2
  as "invocation problem", not specifically "unknown model". A model
  whose record carries **no hashes at all reports `complete`, never
  `valid`, even on a full run** — nothing was validated, only found
  present. An **empty archive exits 0** with an explicit "archive is
  empty (no models)" line: with no records there is nothing to compare
  against, and the message (not the code) carries the fact.
- Verify (re)writes `manifest-sha256.txt` for **every model with a
  readable record — including drifted/invalid ones** (adjudicated
  2026-07-13: the manifest is derived from the record, which stays the
  truth even when disk has drifted; a stale sidecar is worse than a
  fresh one). Lines are `sha256sum -c`-compatible, covering the
  record's hashed files, so fixity is checkable with coreutils alone,
  no tool installed — the survivability property the file exists for.
  The sidecar is derived data (`source: generated`), regenerated on
  each full verify, and never used as input for the audit — the
  record stays the single source of truth. ("Read-only" means payload
  files and records are never modified; writing this regenerable
  sidecar is compatible — confirmed 2026-07-13.) Note: `pull` already
  writes this sidecar at download time (`pull_record.write_manifest`);
  this spec makes verify regenerate it, atomically.
- Hashing multi-GB files takes real time: verify streams files in
  chunks (no whole-file reads into memory) and shows per-model
  progress so a long run is visibly alive. Ctrl-C exits cleanly
  (exit 130) without leaving a partially written sidecar. Refined
  from live use (adjudicated 2026-07-13: result-lines-only left the
  user "staring at nothing" during large hashes): when stderr is a
  terminal, verify prints a `checking <model> (N files, X GiB
  recorded)` line as each model starts and an in-place
  `hashing <file>: done / total` byte counter (redrawn at most twice
  a second) while each file hashes. When stderr is not a terminal
  (cron, pipes), no progress output is emitted at all — the report
  and exit codes stay byte-identical to a progress-free run, so the
  cron contract is untouched.
- The whole-archive report prints **one result line per model,
  valid models included**, then the totals summary (adjudicated
  2026-07-13): an audit should read as "everything was checked", and
  archives are tens of models. Cron reads the exit code.
- `verify -h` shows the command's help (inherited from the root app's
  `help_option_names`); the per-subcommand `-h` guard test's command
  tuple gains `verify`.
- Payload files, `model-record.json`, and record fields (including
  `provenance` and `runtime_tested`) are never modified — verified by
  test: a verify run over a fixture archive leaves every
  non-`manifest-sha256.txt` file byte-identical.
- Models that degrade in the `status` walk (no record, unreadable
  record) appear in the verify report in those same degraded states
  rather than being silently skipped.
- `docs/cli.md` documents the command (categories, exit codes, the
  sidecar's regenerable nature) and the README quick start mentions
  verify in the loop; `docs/data-structures.md`'s "future verify
  spec" annotations are updated to point here.

## Non-goals

- **Repair.** No re-download, no deletion, no record edits. The report
  names what drifted; fixing it is the user's call (re-pull, restore
  from backup, or the future managed remove/retire spec).
- **Hub contact.** Verify is fully offline — it audits disk against
  records, never against the hub. (Re-checking a record against its
  upstream repo is a different, network-facing feature.)
- **Flipping `provenance`.** An `unverified` cache-import artifact that
  passes verify is still `unverified` — that field means "hashes came
  from a pinned hub revision", which a local re-hash cannot establish.
- **Scheduling.** Cron/launchd integration is the user's; the exit
  code and quiet output make it scriptable, nothing more.
- **Archive-wide `manifests/` aggregates.** The reserved top-level
  `manifests/` directory stays empty; this spec's sidecar is per-model
  (metadata travels with the model, per ADR 0001).
- **Interactive model selection.** Verify stays fully non-interactive:
  the archive's contents are known (`status` lists every id), the
  typical caller is a pre-backup script or cron, and a simple
  invocation is what makes "everything was checked" easy to trust. If
  live use shows real friction picking ids, the shared picker belongs
  to the interactive-listing TUI spec (TODO), not here (decided
  2026-07-13).

## Notes

- Cheap-check ordering: existence → size → hash. A size mismatch fails
  fast without hashing; a missing file never attempts a hash.
- The record's `FileEntry.size` is nullable too — a null size with a
  non-null hash still gets hashed.
- Fault domain: an unreadable payload file (permissions, I/O error)
  is a per-file error in the report, not a crash — on a NAS share,
  partial readability is a real state.
- `--quick` was promoted from open question to v1 (2026-07-13): the
  full-hash cost over a NAS link makes a fast structural check worth
  having from day one.
- Review round (2026-07-13), security findings fixed on the branch —
  both PoC-confirmed against the spec's own threat model (a copied
  share the user did not author), both regression-tested:
  - A recorded path that is, or crosses, a symlink leaving the model
    directory is refused and reported as a per-file problem (state
    `invalid`, exit 5) — never followed. Pull writes no symlinks, so
    one where a payload should be is out-of-band drift; following it
    would read (and on mismatch, print the hash of) arbitrary
    host files. Mirrors the symlink refusals every other archive
    surface already applies.
  - The sidecar tmp write uses `mkstemp` (O_EXCL): a pre-planted
    `manifest-sha256.txt.tmp` symlink can no longer redirect the
    write onto an out-of-tree file. Failed writes unlink their tmp.
  - A stat that fails between existence check and size read (NAS
    `ESTALE`-class races) is a per-file problem, not a crash.
  - A failed sidecar refresh (read-only-mounted model dir, full disk)
    is a per-model "manifest not refreshed" warning on stderr; the
    audit continues and the exit code stays payload-driven — a
    read-only mount is a legitimate preservation posture and its
    payloads still verify (adjudicated 2026-07-13, from the
    adversarial review's reproduced crash).
  - Queued to TODO rather than this spec: reserving the tool-owned
    root filenames in record validation (a hand-crafted record naming
    `manifest-sha256.txt` as a payload writes a self-invalidating
    manifest) — it touches `records.py`, which is at the 300-line cap
    awaiting its own split.
