# 0010 — Managed remove

**Status:** shipping
**Last updated:** 2026-07-15

## Goal

Give the archive its one sanctioned deletion path: a `remove` command
that deletes from the archive with the record, the on-disk files, and
the model's staging leftovers handled together. It operates at two
granularities: the whole model (`remove <creator>/<model>`), and a
pattern-scoped subset of a model's payload files
(`remove <creator>/<model> --include '<pattern>'`) — the quant-swap
and shed-the-master pruning cases, using the same pattern vocabulary
as `pull --include`. Today the only way to prune is hand-`rm` inside
the archive root — which leaves the record and directory free to
drift apart, and which silently strands interrupted-pull staging
under `<root>/.staging/<creator>/<model>` (reused on resume, deleted
only on pull success). Real pruning needs exist from first live use;
this closes the create/read/update/delete cycle for the tooling.

## Use cases

Recorded from the 2026-07-15 design discussion — these shaped the
flag surface and should be re-read before any future change to this
flow:

1. **Quant swap** — pulled Q4_K_M, later pulled Q8_0, prune the old
   quant. Pattern removal; the most common expected pruning need.
2. **Shed the master** — archived `--whole-repo` for fine-tuning
   optionality, later decide the disk cost isn't worth it. Pattern
   removal (`--include 'hf-snapshot/*'`).
3. **Mistaken pull / wrong grouping** — files landed under the wrong
   `--model` home (the 0005 footgun, after the fact). Whole-model
   remove of the wrong home, then re-pull correctly; the preview is
   what makes this safe.
4. **Abandoned interrupted pull** — staging leftovers with no model
   directory at all, invisible to `status`. Whole-model remove on the
   id offers to clear the staging directory.
5. **Downsize, don't delete** — keep only the smallest quant of a
   rarely used model. Pattern removal run once per thing to drop; a
   `--keep` inversion flag is deliberately out until live use
   demands it.

## Success criteria

### Whole-model removal

- `remove <creator>/<model>` on an archived model prints what will be
  deleted — formats with file counts and total size (from the record,
  same human sizes as `status`), plus the staging directory when one
  exists — then asks for explicit confirmation. On yes: the model
  directory (record, rendered markdown, manifest, all payload) and
  `<root>/.staging/<creator>/<model>` are both gone, exit 0.
- A model that exists only as staging leftovers (interrupted pull,
  never completed — no model directory) is still removable: `remove`
  reports there is no archived model, offers to clear the staging
  directory, and deletes it on confirmation, exit 0.
- A model directory with no readable record is still removable — the
  confirmation falls back to a filesystem-derived summary (file
  count/size from disk) and says the record is missing/unreadable.
  Degraded metadata must not make a model undeletable.
- After removal, an empty `<creator>/` directory is pruned (in both
  `models/` and `.staging/`); a creator with other models keeps them
  untouched.

### Pattern-scoped removal

- `remove <creator>/<model> --include '<pattern>'` uses the same
  pattern language as `pull --include` — repeatable fnmatch globs,
  case-sensitive, a file selected if it matches any pattern — but
  matches against the record's *archived* payload paths
  (`FileEntry.path`, format-dir-prefixed: `gguf/...`,
  `hf-snapshot/...`), i.e. the paths `show` lists, not the hub repo's
  paths that pull matches. Floating patterns (`*Q4_K_M*`, the shape
  the 0007 resume hint prints) match in both namespaces; a pattern
  anchored at the start of a hub filename will not match on remove.
  Pull's "docs always ride along" rule does not carry over — remove
  deletes exactly what matches, nothing more, docs neither
  auto-included nor protected. The preview
  lists every matching file with its size, states what is kept
  (remaining formats/files and the record), and asks for
  confirmation. On yes: matching files are deleted, their
  `FileEntry`s leave the record, an artifact left with no files is
  dropped, an emptied format directory is pruned, and
  `MODEL-RECORD.md` and `manifest-sha256.txt` are regenerated —
  `verify` on the model passes as `valid` afterwards.
- Because matching runs against record-listed payload paths, the
  tool-owned root files (`model-record.json`, `MODEL-RECORD.md`,
  `manifest-sha256.txt`) can never match a pattern; they are managed,
  not removable targets.
- A pattern matching every payload file is refused with a message
  pointing at plain `remove` — pattern removal never deletes the
  record or empties the model silently.
- A pattern matching nothing errors on exit 2, echoing the pattern
  (mirrors the unknown-model treatment: a no-op delete request is
  user error, not success).
- Pattern removal on a model whose record is missing or unreadable
  errors (exit 1), pointing at plain `remove` — record surgery needs
  a readable record, and the interrupted-removal convergence story
  never produces this state (the updated record is written first and
  stays readable).
- Matching also considers on-disk files in any non-root subtree of
  the model directory that the record does not list (`verify`'s
  `unrecorded` class) — not just the known format-dir names, so junk
  under an unrecognized directory stays removable; root-level strays
  stay unmatched, consistent with the tool-owned exemption. Sizes
  come from disk and the files are flagged as unrecorded in the
  preview. This is what makes an interrupted pattern removal (record
  updated, deletions cut short) finishable by re-running the
  identical command — and it makes stranded junk a pattern names
  removable without resorting to whole-model remove. Symlinked files
  are refused here as everywhere else.
- `--include` is repeatable, as on `pull`.
- Pattern removal does not touch the staging directory (staging
  belongs to in-flight pulls, not to archived payload; whole-model
  remove owns the sweep).

### Both modes

- Declining the confirmation (or Ctrl-C at the prompt) deletes
  nothing and exits 0 ("nothing removed" is a successful outcome,
  matching pull's declined size confirmation; implemented as an
  explicit branch, not `typer.confirm(abort=True)`, which exits 1).
- Ctrl-C *during* deletion (after the confirmation) exits 130 and
  prints the exact re-run command as the final line (0007
  precedent). There is no resume state and no separate continue
  command: re-running the same invocation finishes the job —
  whole-model via the no-readable-record path, pattern mode via the
  unrecorded-file matching above.
- On a TTY, deletion prints one line per file as it is removed (the
  0009 live-use lesson: no staring at nothing on slow media —
  removal over NFS is where this shows). Progress lines gate on
  `stderr.isatty()` and go to stderr, as verify's progress does;
  stdout stays byte-identical between TTY and non-TTY runs: the
  preview block plus the result line, exactly as a script logs it.
- `--yes` skips the confirmation for scripted use. The scripted
  output is the interactive output minus the prompt line: the full
  preview block still prints, then the result line — a destructive
  operation always discloses what it deleted, so script logs carry
  the audit trail. `--yes` skips the question, not the disclosure.
  The prose output is not promised machine-parseable; exit codes
  carry the scripting contract (0009 stance), and `remove` joins the
  queued `--json` work only if a need appears.
- Non-TTY without `--yes` refuses on exit 2 rather than hanging on a
  prompt.
- Unknown `<creator>/<model>` errors on exit 2 (the user-input
  domain, per 0009), with the same close-match self-correction hint
  style `verify --model` uses.
- `status` no longer lists a removed model and `verify` reports
  nothing for it; after pattern removal both reflect exactly the
  surviving files. No file outside `models/<creator>/<model>` and
  `.staging/<creator>/<model>` is ever touched. Tests use tmp
  archives, never a real one.
- The argument is validated against the same strict
  `<creator>/<model>` pattern as `show` before any path is
  constructed, and a symlinked model directory (or symlinked payload
  file) is refused (consistent with the defensive-read rules) —
  remove must never follow a link out of the archive root.
- `docs/cli.md` documents the command, both granularities, the
  confirmation behavior, and exit codes in the same change.

## Non-goals

- **No `--format` flag**: a pattern already expresses it
  (`--include 'hf-snapshot/*'`); one selection vocabulary, not two.
- **No `--keep` inversion** (use case 5): run pattern removal per
  thing to drop until live use demands the inverse.
- **No retire/tombstone mode** (delete payload, keep the record as
  history). The TODO title says "remove/retire" — this spec reads
  that as one `remove` command; a metadata-preserving retire is out
  until a live need shows up.
- No trash can, undo, or soft delete — deletion is permanent; the
  confirmation preview is the safety mechanism.
- No bulk removal (patterns across models, multiple models per
  invocation, `--all`).
- No hub interaction — remove is purely local.
- No archive-wide orphan sweep (staging entries nobody asked to
  remove); a future `gc`-style command could own that.

## Notes

- **Crash-safety ordering inverts the write convention** ("source of
  truth last" becomes "source of truth first"). Whole-model: delete
  the record first, then payload, then staging — a crash leaves an
  unrecorded directory, which `status`/`verify` already surface as a
  visible degraded state and which a re-run can finish (covered by
  the no-readable-record criterion). Pattern removal: write the
  updated record first, then delete the de-listed files — a crash
  leaves extra files on disk, which `verify` reports as informational
  `unrecorded`, never a record naming missing files (which reads as
  corruption).
- **Media speed is a non-concern for duration, a real concern for
  the abort window.** Removal is metadata work — one `unlink` per
  file plus directory removal — so cost scales with file count, not
  bytes; even a whole-repo snapshot over NFS is seconds, and remove
  needs none of verify's throttled byte-counter machinery. What slow
  media widens is the chance an abort lands mid-deletion, which the
  source-of-truth-first ordering plus re-run convergence absorb. A
  *dead* NFS mount can block the process in uninterruptible sleep
  where Ctrl-C never lands — an OS property, out of scope.
- Pattern matching runs against `FileEntry.path` strings, not the
  record's `quantization` field — that field is never populated (a
  standing TODO item); when it grows a per-file shape, `--quant`
  sugar can layer on top of the same removal core.
- The don't-touch rule ("never delete archive contents") stays; this
  command becomes the single exception, which is exactly what
  "managed" means here.
