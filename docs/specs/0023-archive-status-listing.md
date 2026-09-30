# 0023 — Archive Status In File Listings

**Status:** shipped (PR #44)
**Last updated:** 2026-09-30
**Depends on:** 0018, 0021

## Goal

Show which files are already archived while a human chooses files to
pull, including the `discover` handoff. A large quant repository can
contain many expensive alternatives; the directory listing should make
complete and partial local coverage visible before the human types a
pattern. This supports the explicit human selection in
[`0000-product.md`](0000-product.md) using the existing text interface.

## Success criteria

- The shared interactive file-selection flow for `pull` and `discover`
  identifies the active archive and explains that annotations use
  archive records and local file metadata, without fresh checksum
  verification.
- Directory and root shard-set rows show coverage such as
  `[archived 3/3]`, `[archived 2/6]`, or `[archived 0/6]`. Counts use
  the files represented by that row, including nested members, and
  never infer completion from a directory's existence.
- Individual rows, including the expanded listing behind `f`, show
  corresponding per-file status. Existing file-kind notes remain
  visible, and both rolled-up and expanded listings agree.
- A file counts as archived only when the active archive has a matching
  record for this source repo and file, the recorded payload is present,
  and available local size and upstream identity metadata agree. Use
  existing record and pull-matching conventions rather than inventing
  a separate identity rule. When upstream SHA256 is unavailable, clearly
  disclose the weaker metadata comparison; never label it freshly
  verified.
- Recorded-but-missing files do not count as archived. Known local size
  mismatches, changed upstream metadata, and unavailable status are
  distinguishable from an ordinary file that has never been archived.
  Group annotations expose these exceptions rather than hiding them
  behind a coverage count.
- Status lookup respects existing source-path and archive-layout rules,
  including selective and whole-repo payloads and documentation paths.
  A copy belonging to another source repo, an unrecorded file, a runtime
  cache, or a staging file cannot earn an archived marker.
- Documentation stored at a destination that could represent either a
  relocated document or a verbatim nested document is unavailable,
  even when only one possible source path appears in the current Hub
  listing. This includes ordinary selectively archived documentation;
  records lack the original-path/layout evidence needed to disambiguate
  it. A separate unambiguous matching copy can still count. Weight
  coverage is unaffected. Human approved this review decision.
- Lookup is read-only and bounded to the selected repo in the active
  archive. It does not read or hash weight payloads, scan unrelated model
  directories, make additional Hub requests, or create archive files.
- Missing or unreadable evidence never produces a positive archived
  claim. An unavailable annotation does not prevent the human from
  selecting files; existing pull planning remains authoritative for
  integrity checks and error handling.
- All rows remain selectable through the existing pattern syntax.
  Ordering, paging keys, quit behavior, and download/skip decisions stay
  intact. Header, legend, and suffix wrapping are included in terminal
  frame sizing so annotations do not displace the selection prompt.
- Tests use temporary archives, tiny payloads, and fake Hub metadata.
  Cover complete and partial groups, no record, missing payloads,
  changed and unknown metadata, source isolation, both archive layouts,
  both command entry points, and narrow terminal frames. No mounted
  model archive or weight download is required.
- Update `docs/cli.md` with the marker meanings and their limits.

## Non-goals

- Fresh integrity verification, loading a model, or changing the archive
  schema, planner, transfer behavior, or documentation refresh policy.
- Cache inventory/import, staging progress, or searching other archives.
- Hiding, disabling, ranking, or automatically selecting files.
- A new TUI, arrow-key interaction, pattern-match preview, or remaining
  download-size preview.

## Notes

- Apply annotations to the existing non-TTY flat listing as well as
  terminal listings; preserve its single-pass output and input
  semantics. No new flag is needed.
- If archive metadata cannot be read safely, show one concise
  status-unavailable explanation and continue selection. Do not
  interpret unreadable evidence as zero archived files.

- Display status is a point-in-time observation. The later pull plan
  must still inspect current evidence; annotations are not a substitute
  for that check.
- Destination layout can depend on the selected files. Planning must
  account for this explicitly rather than guessing a payload path before
  selection or promising that any archived copy necessarily avoids a
  transfer to a different layout.
- An archive not yet converted to per-repo directories (ADR 0003) shows
  every file unavailable: the per-repo lookup cannot see a repo's files
  filed under another repo's directory, so its answer would be a
  confident, wrong `not archived`. Added at the second review round.
- Existing unrelated changes to `.claude/rules/git-workflow.md` are
  outside this feature and must be preserved.

## External references

None — original presentation behavior over existing repository record,
listing, and pull-planning contracts. No new external API or format
contract is introduced.

## Phase handoff

- 2026-09-30: Human approved the spec, including both assumptions about
  non-TTY output and unavailable status. Read-only planning is complete;
  the human approved the plan below, including its 15-file scope.
  Implementation followed 47 failing lookup/render tests and five
  failing CLI tests. A malformed source URL regression also failed
  before its fix. Geometry coverage was added during implementation.
- 2026-09-30: 81 new tests and 443 new/existing listing tests pass.
  A real 80x24 PTY with tiny temporary payloads confirmed complete and
  partial summaries, expanded markers, paging, return to summary, and
  clean quit. Full verification and independent reviews are next.
  Work is uncommitted; no PR exists and the spec remains `shipping`.
- Review checkpoint: the first full gate passed (1,610 tests, two opt-in
  Hub canaries skipped; Ruff and native/Linux mypy clean). Both reviewers
  reproduced documentation-path aliasing. Six new regressions cover the
  same recorded destination claimed by two current Hub paths, and pass
  after conservative ambiguity handling. A historical variant remains:
  removing the old source path upstream must not make its relocated
  payload count as a newly introduced nested document. The human approved
  marking structurally ambiguous documentation destinations unavailable,
  including ordinary selectively archived docs. The correction and its
  regression tests are complete. Weight status is unaffected.
  Security review found no additional issue.
- Final verification: 96 feature tests pass; the full suite passes
  1,625 tests with two opt-in live Hub canaries skipped. Ruff lint and
  format, native/Linux mypy, and close-out bookkeeping checks pass.
  Historical-alias regressions failed before the correction. The
  adversarial reviewer independently confirmed both historical removal
  directions now report unavailable, while a separate unambiguous copy
  still earns credit. No real archive was mounted or modified.
  Implementation remains uncommitted on `spec-0023-archive-status-listing`;
  no PR exists. The unrelated workflow-rule edit remains untouched.
- Final independent standard and adversarial reviews report no remaining
  findings; each reran the 96 feature tests. The final manual security
  review reports no additional findings. Ready for human inspection
  and an explicit commit instruction.
- Second review round: both reviewers mutation-proved seven guards that
  survived deletion. Those were the roll-up and fits-path `*context` frame charges,
  secondary-issue markers on rows and groups, the upstream-size fallback,
  and header scrubbing. Each now fails a test under its mutation; the
  geometry sweep covers widths 30-120 and heights 14-60. The adversarial
  reviewer found that `discover` on an unconverted archive labelled a
  quant `[not archived]` before pull's gate refused. Reproducing it
  exposed a pre-existing crash: the gate's error escaped `run_pull` at
  exit 1. The picker now withholds status (`picker_status`) and
  `run_pull` maps the gate to exit 2, both mutation-proved. One docs
  example that showed a relocated README as `size only` is corrected.
  105 feature tests; full suite 1,634 passed, two canaries skipped.
- Committed and opened as PR #44 on the human's instruction; the
  workflow-rule edit rode along with the human's consent.
- Follow-up (2026-09-30, `fix/doc-status-label`): live use showed that
  `[status unavailable]` on a pick-files README read as "missing or
  unknown". The human chose `[recorded: comes with every pull]` for the
  ambiguous-document case, which states what the record shows and why it
  cannot matter to the pattern. The ambiguity rule itself is unchanged:
  such a copy still earns no `[archived]` credit and no disk check. An
  attributed copy elsewhere no longer carries the alias as a problem.
  Same follow-up: a group row with no archived members now reads
  `[not archived]` instead of `[archived 0/N]` (human's call). Any
  exceptions are still listed after it, and an unreadable record still
  shows `[status unavailable]`, never either form.

## Implementation plan

### Files to touch

Production code:

- `src/llm_preserver/archive_status.py` **(new)** — read-only lookup and per-file evidence classification.
- `src/llm_preserver/cli/pull_exec/listing/status.py` **(new)** — marker, group-summary, archive-header, and legend rendering.
- `src/llm_preserver/cli/pull_exec/listing/rows.py` — retain group members and append annotations while preserving companion notes.
- `src/llm_preserver/cli/pull_exec/prompts.py` — display annotations across flat, summary, and paged listings; include new text in frame budgets.
- `src/llm_preserver/cli/pull_exec/flow.py` — provide the active archive and status snapshot to the shared picker.

Tests, each **new** and kept below 300 lines:

- `tests/test_archive_status.py` — matching metadata and both archive layouts.
- `tests/test_archive_status_edges.py` — missing, changed, inaccessible, unsafe, and foreign-source evidence.
- `tests/test_pull_listing_status.py` — per-file and group rendering.
- `tests/test_cli_pull_archive_status.py` — `pull`/`discover`, non-TTY output, continued selection.
- `tests/test_cli_pull_archive_status_geometry.py` — annotated frame sizing and navigation.

Existing compatibility assertion (identified by the full suite):

- `tests/test_cli_pull_decline.py` — retain the windowed quit assertion
  without assuming the pre-annotation page size of exactly 20 files.

Documentation:

- `docs/cli.md` — markers and limitations.
- `docs/specs/0023-archive-status-listing.md`, `docs/specs/README.md`, `TODO.md`, `CLAUDE.md` — approved status, dashboard, and required feature bookkeeping.

**Scope: 15 files, including five new test modules and existing spec bookkeeping.** Plan approval also approves exceeding the repository's five-file threshold.

Implementation adjustment: 16 files including the existing quit test's
page-size assertion. Archive explanation text legitimately consumes
frame space; this assertion update preserves its original windowed-quit
contract. No additional product behavior or scope was introduced.

### Order of operations

1. Delegate failing tests to `test-first`; show the failures and cross-check coverage against the approved spec before implementation.
2. Implement a snapshot using the already-fetched `RepoInfo`. Read only `model_dir_for(archive, repo_id)` and its bounded `load_record`; never call the pull planner, hash payloads, scan other models, or request additional Hub metadata.
3. Resolve recorded candidates using artifact formats and `checked_target_path`, covering selective documentation relocation and whole-repo paths. Require matching source attribution; use the existing legacy no-source convention only when the record's `hub_id` matches. Exclude runtime/staging copies and unrecorded payloads. Validate containment before inspecting local metadata.
4. Classify evidence:
   - Matching recorded/upstream SHA256 and compatible available sizes: archived.
   - Without comparable hashes, matching known sizes: archived with an explicit weaker-comparison marker.
   - Missing payload, local size mismatch, changed upstream metadata, or insufficient/unreadable evidence: distinct non-positive statuses.
   - No matching record: not archived.

   Multiple layouts count a Hub file once; conflicting candidate evidence remains visible. A missing record differs from a record that cannot be read.
5. Render directory/root-shard counts from their actual members, with exception counts and weaker-comparison disclosures. Identify the archive and explain that status is a metadata observation. Feed headers, legends, and annotated row widths into existing frame calculations; preserve ordering, selection syntax, and navigation.
6. Update documentation and run the full `/review-check`, including closeout checks, then independent `/review`, `/review-adversarial`, and `/security` for untrusted metadata/path handling. Show the diff and verification results before any commit.

### Risks / open questions

- **Layout depends on selection.** An archived marker means a matching copy exists in this repo's recorded payloads; it does not promise the eventual pull will skip a transfer into another layout. Document this explicitly.
- Metadata checks cannot detect same-size local corruption. Unknown sizes must never match merely because both are absent.
- Existing tiny-terminal minimum-row behavior remains; test forward progress where even frame chrome cannot fit.
- `rows.py` and `prompts.py` approach the 300-line cap. Keep new logic in the named modules; avoid broader refactoring.
- No unresolved product assumptions: annotate non-TTY listings and continue selection when status is unavailable.

### Out of scope (won't touch)

Download planning, transfer/skip decisions, schema migrations, dependencies, fresh verification, model loading, cache import, automatic selection, and the unrelated `.claude/rules/git-workflow.md` edit.
