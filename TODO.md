# TODO

What's next, in rough order. Feature detail lives in
[`docs/specs/0000-product.md`](docs/specs/0000-product.md) (roadmap)
and the numbered specs; this file is the short-term working list.
Check items off as they ship; update when priorities shift.

## In progress

- [ ] **0016 artifact classification and lineage** — specced and
  planned 2026-08-06; ADR 0002 accepted. Taxonomy settled
  (`adapter | quant | full-weights | unknown`), every design question
  resolved in the spec's `## Implementation plan`. Shipping in two
  PRs: **(a) docs + ADR + spec**, which document behavior that already
  exists and need not wait, then **(b) implementation** as four
  checkpointed passes — schema v3, classifier + pull wiring,
  derivation + prose, hardening + docs sweep. Phase 2 (`status` shelf
  view, criterion 4) follows separately. Companion doc
  [`docs/what-to-archive.md`](docs/what-to-archive.md) answers the
  live question ("I have a Q4 — what else do I need?") against today's
  tool.



## Hardening (independent of any spec)

- [ ] **`specs-status.sh` does not strike through a `shipped (PR #N)`
  spec** — found alongside the `(blocked)` bug fixed on spec 0018's
  branch, 2026-08-12. The strikethrough `case` at
  `.claude/hooks/specs-status.sh:114` matches the bare string
  `shipped`, so 0013, 0014, and 0015 render live in the dashboard
  while 0001 and 0017 render struck through — the difference is only
  whether the spec recorded its PR number. Same one-line shape as the
  `(blocked)` fix (`shipped | shipped\ *`). Left off 0018's branch
  deliberately: that branch caused the `(blocked)` falsehood and fixed
  it, but this one is pre-existing and would restyle four unrelated
  dashboard rows in a listing diff.

- [ ] **EOF at the interactive file-listing prompt exits 1, not 2** —
  found by spec 0018's plan round, 2026-08-12, pre-existing.
  `prompt_for_selection` calls `typer.prompt` with no
  `except typer.Abort`, and `run_pull`'s try block catches only
  `KeyboardInterrupt` / `ArchiveError` / `PullError`. So a
  non-interactive pull that reaches this prompt dies with click's
  standalone `Aborted!` on stderr and exit 1 — exactly the
  "undocumented exit 1" that `confirm_or_stop`'s own docstring
  (`cli/pull_exec/prompts.py:24-26`) says scripted pulls must never
  hit. Every other confirmation on this flow maps to `PullUserError`
  and exit 2. Fix is one `except typer.Abort` naming the bypass
  (`--include` or `--whole-repo`), plus a row in `docs/cli.md`'s exit
  table. Deliberately **not** taken on spec 0018's branch: it is a
  behavior change to a shipped path and deserves its own test and
  changelog line rather than riding a listing change.

- [x] **`remove` could not delete locked payload on an SMB archive** —
  fixed 2026-08-11. `remove/execute.py` unlinked with a plain
  `Path.unlink()` and no file-flag handling; ADR 0001's payload lock
  comes back as the BSD immutable flag (`uchg`) over SMB, and
  `unlink(2)` on an immutable file fails with EPERM. Because `remove`
  deletes the record *first* (crash-safe order, spec 0010), it would
  have destroyed a model's source of truth and then failed on its first
  weight, orphaning the payload. Found while fixing the same bug in
  `migrate`; both now share `file_locks.py`, and
  `tests/test_remove_immutable.py` pins that nothing survives under
  `models/`. Pre-existing on `main`, unrelated to spec 0017's design.

- [ ] **`discover`'s tree frame overruns the terminal — spec 0015 bug,
  found in live use 2026-08-11** (the live terminal check session 18
  deferred). `tree_chrome_lines`
  (`src/llm_preserver/discover_render.py:179`) charges the header,
  breadcrumb, ancestry ladder, `down —` label, pull line, footer,
  prompt and a 2-line reserve — but **not the per-relation section
  labels** (`quantized versions:` etc.) that `render_tree_page` prints
  at line 264, nor the leading separator. Measured on a real 187x63
  terminal: `Qwen/Qwen3-Coder-30B-A3B-Instruct` has three relations, so
  the frame printed 62 lines against a budget that believed 56 rows
  fit; with a two-line shell prompt that is 64 on a 63-line screen and
  the top scrolls away — exactly what windowing exists to prevent, and
  unrecoverable under `screen`. Fix: charge one line per distinct
  relation label in the visible window (they are known before the frame
  renders) plus the separator, and re-check the reserve. Same class as
  session 18's own finding — count *physical* lines — missed because
  every test used a single relation, where one label hides inside the
  reserve. Belongs on its own branch, not spec 0017's.

## Next spec (0020) — pick one

- [ ] **Runtime views, later phases** (spec 0002; phase 1 shipped,
  PR #20 — see Shipped): LM Studio / llama.cpp / vLLM adapters over
  the same core, plus the phase-1 deferrals (sharded-GGUF linking,
  `--model` scoping). Candidate addition (researched 2026-08-01):
  **Unsloth Studio** — Unsloth's local inference app (beta;
  installer-based, not the `unsloth` pip fine-tuning library). Runs
  GGUF/safetensors via llama.cpp, serves OpenAI/Anthropic-compatible
  APIs, and detects models from the HF hub cache or a user-selected
  folder — so unlike Ollama, no manifest/blob synthesis: the adapter
  is likely just a directory of symlinks to archived GGUFs (same
  shape as the planned LM Studio / llama.cpp adapters). Hold until
  it leaves beta before pinning tests to its layout.
- [ ] **Smoke test**: load an archived model offline in a local
  runtime (llama.cpp / ollama), check a trivial deterministic
  prompt, record the result in the record's `runtime_tested` field
  (a 0000 success metric: the archive is *tested*, not just
  downloaded). Pairs with runtime views — views make models
  loadable in place, smoke test proves they load.
- [ ] **Interactive listing TUI** (promoted from smaller items —
  see its entry below for scope): after 0006's live testing, the
  numbered-pick UX is workable but the scroll pain is real. Spec
  0015 took the `discover` half of this (windowed frames, `b` to
  step back, numbers that never renumber) with no new dependency,
  and spec 0018 takes `pull`'s file listing (directory roll-up plus
  a paged full listing behind `f`). What remains for a TUI is the
  nice-to-haves a plain-print flow cannot do — arrow-key highlight,
  type-to-filter — plus the match-preview loop declined at 0018's
  design checkpoint (show what a typed pattern matched, and let the
  human accept or edit it before the plan runs).
Artifact classification and lineage moved out of this list on
2026-08-06 — it is now spec 0016, above. Two ideas from the original
queue entry did **not** make the spec and stay open here:

- [ ] **Canonical-roots shelf**: promote common roots (Qwen / Llama /
  Mistral base and instruct) into a curated set worth holding on their
  own, independent of what derives from them. Dropped from 0016 as
  curation policy rather than classification.
- [ ] **Size-ratio threshold** as an adapter marker ("safetensors far
  smaller than the claimed base"). 0016 keeps it only as a fallback
  behind `adapter_config.json` and requires a stated threshold plus an
  `unknown` verdict; if the config marker proves sufficient in
  practice, drop the heuristic entirely.

## Shipped

- 0019 pull staging cleanup (PR #33): a pull that archived every byte
  could still report failure. The staging delete sat *inside* the `try`
  whose `except OSError` raises `PullEnvError` (exit 3), and it runs
  after `write_manifest` and `save_record` — so a cleanup that could
  not finish inverted the tool's central promise, telling the human
  their pull failed when the bytes, hashes, record, and manifest all
  landed. Live trigger: a 160 GiB `Qwen/Qwen3-Coder-Next` snapshot ran
  23:32→04:58, archived all 40 shards, then left 42 files (2.4 KiB of
  pure `.lock`/`.metadata` bookkeeping) in `.staging/`. Root cause
  confirmed from the host's kernel log, not inferred: a `DarkWake from
  Deep Idle` at 04:42 dropped the SMB session twice, and the reconnect
  could not reopen the **durable handle on
  `model-00026-of-00040.safetensors.lock`** — the exact file the
  residue starts at. macOS smbfs renames a still-open file to a hidden
  placeholder instead of unlinking it, so the parent `rmdir` answers
  `ENOTEMPTY`; reproduced 10/10 clean vs. failing-with-one-open-fd on
  the live share. Fix: cleanup moves outside the try (warn once naming
  the leaf and errno, saying the archive is *complete*, exit 0).
  Emptied creator dirs go by `Path.rmdir` (never `rmtree`), so a
  sibling model's staging is structurally safe, and only a pull that
  actually downloaded something deletes a leaf — both mutation-proved.
  **The drafted second half was cut at review**: the spec 0014 no-op
  path was going to clear a leaf holding nothing but `.cache/`
  bookkeeping, making residue self-healing. The adversarial round
  killed it with a reproduction — huggingface_hub writes its
  `.cache/huggingface/` scaffolding *before* its first network call,
  so a **running** pull's leaf is indistinguishable from dead residue,
  and a second pull in another terminal deleted the live leaf and
  killed the first pull with the very exit 3 this spec removes. Three
  findings compounded it: a hub repo can ship its own `.cache/`
  directory (the tool archives `gguf/.cache/params.json`); the
  `.incomplete` protection is largely vestigial in huggingface_hub
  1.24.0, which unlinks partials in a `finally` and never resumes; and
  the recovery needed the hub, so an offline re-pull could not clear a
  repo that is by definition already archived. Residue now stays until
  a human deletes it, and the warning and docs say so instead of
  promising a self-heal. Cutting the guard also removed the only two
  external-authority constants, so nothing shipped depends on hf's
  local-dir layout. `verify --staging` deliberately unchanged — spec
  0012's whole-leaf counting exists so a mid-download `.incomplete`
  cannot hide. New `staging_cleanup.py` is where *pull* deletes inside
  `.staging/` (`remove` keeps its own path); `model_scan.py` stays
  read-only. 1327 tests.

- 0018 pull file listing window (PR #31): the interactive file listing
  pages instead of walling. Live trigger — `discover 'kimi k3'` →
  `1 = pick files` printed 171 rows into a 24-line terminal, one stage
  after discover's own windowed frames, which spec 0015 had fixed while
  naming this listing out of scope. Windowing alone was the wrong
  answer: the stage prompts for a *glob*, and what decides the glob is
  which quant directories exist, so an overflowing listing opens on a
  directory roll-up (171 rows → 14) with every file one `f` away, paged
  `m`/`b`, `s` back to the roll-up keeping your place. Piped runs and
  repos that fit are byte-identical to before. Groups sit in hub order
  at their first member's slot, counts and sizes are exact sums, no
  kind note is inferred for a directory, and an unreported size makes
  the header say `at least` — the abandoned spec that previously held
  this number died of grouping that suppressed facts, and none of that
  recurs. Carried by two neutral refactors: `cli/window.py` (moved up a
  package, `is_interactive` public) and `text_window.py`, which now
  owns the single physical-line rule with `fit_rows` as its adapter.
  Review round found the headline criterion failing at 42-43 columns —
  the frame was sized against `footer_line(1, …)`, the narrowest first
  index, so the real footer wrapped and frames hit 25 rows on a 24-row
  screen; the test could not catch it because it was hardwired to 80
  columns. 1287 tests.

- 0017 per-repo model directories (PR #27): `models/<owner>/<repo>`
  mirrors the hub repo id verbatim, replacing ADR 0001's
  canonical-model grouping — so a pull's destination is a pure function
  of the id you type, with no inference, no grouping prompt and no
  metadata call. Lineage moved into the record (`base_model` plus who
  claimed it) where `status` groups by it, `show` reads it both ways
  and `MODEL-RECORD.md` says it in prose. `migrate` converts an archive
  in place or into a scoped `--to` copy, re-downloading and re-hashing
  nothing because paths *inside* an artifact do not change; `verify`
  reports a breach as `unmigrated` appended to the fixity word, drift
  still outranking layout in the exit code. Converted the live archive:
  11 directories, 682.6 GiB, in seconds — verified 33/33 complete, no
  payload lost or double-counted, zero empty directories, every
  manifest correct, and criterion 18 closed by serving bge-m3 through a
  regenerated view store. Thirteen defects surfaced: seven from live
  use (nested removals, EPERM on `uchg` payload in `migrate` and again
  in `remove`, a record understating its schema, metadata flags dropped
  on a no-op pull, a `discover` frame overrun, and a silent manifest
  rewrite), six from the review round. 1086 tests.

- 0015 discover paging windows + stable pick numbers (PR #25): both
  listings now render one terminal-sized window instead of reprinting
  the whole accumulated list on every `m`, and rows are numbered once
  as they arrive so a number permanently names a repo. The live-use
  trigger was scroll pain under `screen`; the bug underneath it was
  worse — the tree re-sorted children by relation each loop, so a new
  quantized page displaced already-numbered finetune rows and **60 of
  80 numbers named a different repo after one `m`**, silently. `b`
  steps back a page with no network call; fetching is decoupled from
  display (one page feeds several windows, buffer tops up before a
  window underfills) with 0006's per-relation granularity untouched.
  Sizing counts *physical* lines — real hub ids wrap at 80 columns, so
  a logical-line budget promised one screen and delivered two. Review
  round (three reviewers, converging) caught the footer advertising
  numbers the prompt refused, runt one-row frames, and a
  detached-stdout traceback. 857 tests.
- 0014 skip confirmations on a nothing-to-do pull (PR #23): a re-pull
  whose whole selection is already archived under a *user-chosen*
  home (the typed repo id, or `--model`) asks no questions — the plan
  runs first, and with nothing to download or adopt, y and N reach
  the same no-op. Final line says "already archived ... nothing new
  to pull"; non-interactive complete re-pulls exit 0 (were 2), so
  scripted re-pulls are idempotent. A hub-derived home (declared
  base_model) still confirms before naming any directory — the 0006
  invariant, upheld after a review PoC showed a hostile base_model
  plus a name+size-matched hashless file could steal a silent
  "already archived" exit 0. Riders: adapter-config advisory fetch
  requires a declared size; `pull_metadata.py` split off.
- 0013 Ollama match (PR #22): `discover --match-ollama <name[:tag]>`
  states byte-identity facts between a locally-run Ollama model and
  hub GGUFs — local manifest digest (read-only, fixed-order store
  probe, disclosed) against per-file LFS SHA256s from the existing
  seam, candidates in hub order, matches in a footer whose last line
  is the exact pasteable `pull --include` command plus the repo's hub
  facts for provenance picking. `--search` and `--limit` (max 500)
  are the levers; no ranking, no auto-pull. Plus the 0011 deferral:
  Ollama-shaped ids pasted into `pull` get the recovery command
  appended to the clean invalid-id error. Live-verified on the real
  store/hub (bge-m3: one match at depth 20, six identical at 500);
  four live-use adjudications shaped the output.
- 0002 runtime views, phase 1 (PR #20): the `views` command — a
  record-driven eligibility scan (GGUF + recorded SHA256s, every skip
  reasoned) and an Ollama adapter that seeds a disposable external
  store: blob symlinks named by recorded digests plus tool-synthesized
  manifests/config blobs, so archived models `ollama list` and serve
  in place with zero payload copied (`OLLAMA_MODELS=<dest>
  OLLAMA_NOPRUNE=1`). The drafted seed-and-delegate design died on its
  gating live test — ollama 0.32.0 `create` rewrites GGUF layers into
  a new full-size blob — and the spec's synthesized-manifest fallback
  was implemented and live-verified end to end (12 KB store over a
  1.15 GB model, real embeddings served through the symlink). Review
  round PoC-confirmed and fixed: forged/foreign view markers granting
  rmtree rights, a marker-symlink archive write, lexical prune
  containment, relative-archive-path dangling links, unquoted paste
  commands. 636 tests.
- 0012 staging-leftover detection (PR #18): `verify --staging` — a
  hash-free scan of `.staging/<creator>/<model>/` that surfaces
  abandoned downloads an interrupted pull left behind (partial bytes,
  no record) which the record-based audit is structurally blind to.
  Deep view via the flag, plus a one-line informational footer on
  plain/`--quick` verify; always read-only, resolution via `pull`
  (resume) or `remove` (discard). Counts the whole staging leaf, hf
  `.cache/` bookkeeping included, to surface all incidental space and
  let the human decide. Live-use trigger + validation, 2026-07-19: found
  four genuinely abandoned pulls on a real archive (none archived per
  `status`). Adversarial review caught two traceback-on-unreadable-
  `.staging/` bugs, both fixed + regression-tested. 595 tests.
- 0011 clean error on invalid repo id (PR #15): a bad Hugging Face
  repo id — the common case is an Ollama `name:tag` pasted into `pull`
  (`qwen3-vl:30b-a3b-instruct`) — now prints a clean one-line error and
  exits 2 instead of a rich Traceback. `HFValidationError` (a
  `ValueError` subclass) was escaping the hub seam's `MAPPED_EXCEPTIONS`
  unmapped; it now maps to `PullUserError`, deferring the validity
  verdict to the library's own validator so no valid id is
  over-rejected. Live-use trigger, 2026-07-15. Error tests split into
  `test_cli_pull_errors.py` for the 300-line cap.
- 0010 managed remove (PR #14): the `remove` command — whole-model
  and `--include` pattern-scoped deletion, the archive's one
  sanctioned delete path (record + files + `.staging` kept
  consistent). Preview-then-confirm, `--yes` skips the question not
  the disclosure, a non-interactive run without `--yes` refuses,
  crash-safe by deleting the source of truth first, Ctrl-C reprints
  the re-run command. Prep landed first: `records.py` → `records/`
  package and the tool-owned-filename reservation. Review round
  PoC-confirmed and fixed three symlink-escape vectors on a copied
  archive plus a pattern-mode record/disk mismatch; `remove.py` split
  into a `remove/` package. 562 tests. Deferred to smaller items:
  artifact/`--format` pruning, retire/tombstone mode.
- 0001 archive init + records, 0003 selective pull, 0004 full
  snapshot (`pull --whole-repo`, shipped as `--all` and renamed by
  0005). The core loop works end to end and is live-verified: init →
  pull quants and masters → status/show.
- 0005 companion advisories + `pull --plan` (merged 2026-07-13,
  rebase-merge): archive-aware advisory rules (companions, shard
  sets, adapter base, full-precision master, `--model` grouping
  mismatch as a highlighted warning), the `--plan` dry run,
  `--all` → `--whole-repo`, size confirmation + disk preflight on
  every pull mode. Live-verified against real Qwen3.6 repos,
  including the copy-pasted `--model` footgun it now catches.
- 0006 guided discovery (merged 2026-07-13, PR #7, rebase-merge):
  the `discover` command — hub search passed through verbatim →
  model-tree navigation (ancestry ladder, breadcrumb, stable `0`
  pull key, archive-mode choice) → the unmodified pull flow, with
  declared base models rename-resolved (one disclosed light call)
  so records carry current ids. Hub seam extended (search/children/
  summary) and `hub.py` split into a package. Fifteen live-use
  adjudications from manual testing shaped the UX; the full record
  is in the spec.
- 0007 resume-command hint (merged 2026-07-13, PR #9, rebase-merge):
  interactively shaped pulls (discover handoff, interactive file
  listing) print the exact direct `pull` command after the
  confirmations — absolute archive path, quoted `--include`
  patterns, the confirmed grouping replayed as `--model` — and
  Ctrl-C during *any* transfer reprints it as the final line (exit
  130). Hub repo ids validate before entering the pasteable line;
  `pull_exec.py` split into a package. Live-verified twice,
  including the interrupt-a-resumed-pull case that flipped the
  Ctrl-C print to unconditional. README now documents
  `uv tool install --editable .` (the hint assumes the CLI is on
  PATH).
- 0009 verify (shipped 2026-07-13, PR #12): the whole-archive fixity
  audit — complete (files present) vs valid (SHA256s intact), with
  existence → size → hash fail-fast ordering, `--quick` (structural
  check in seconds, never claims valid), `--model` scoping with
  unknown-id self-correction, and the exit-code cron contract
  (0/1/2/5/130; unhashed/unrecorded are informational). Full runs
  atomically regenerate `manifest-sha256.txt` from the on-disk
  record bytes so `sha256sum -c` passes with coreutils alone. Live
  progress on a TTY only (checking line per model, in-place byte
  counter per hash); cron output byte-identical to progress-free.
  Security round: symlinked/escaping recorded paths refused as
  drift; sidecar tmp write is O_EXCL. A read-only-mounted archive
  verifies with a warning instead of crashing.
- 0008 `--hf-logging` (shipped 2026-07-13, PR #11): vendor-telemetry
  passthrough on `pull` and `discover` — `RUST_LOG=info` set at
  command startup only when unset (an inherited filter wins, with a
  notice naming it), `huggingface_hub` raised to exactly info (debug
  unreachable by any flag), no self-identification to the hub. One
  activation line because healthy transfers are provably silent at
  info; the 0007 resume hint replays the flag. A tripwire test pins
  `hf_xet`'s lazy import — the ordering the whole flag rests on.

## Smaller items (from live use)

- [ ] **`verify --staging --clean`** (queued from spec 0019,
  2026-08-13): 0019 leaves cleanup residue on disk until a human
  deletes it, so `verify --staging` keeps listing a fully archived
  model. The automatic clear was designed and cut — see the 0019
  Shipped entry — because an implicit delete cannot tell a running
  pull's staging leaf from dead residue. An explicit, human-present
  verb can: the human is there, and it can refuse anything that is not
  provably disposable. Two decisions to make first, neither of them
  code: `verify` is read-only by design (specs 0009/0012), so this
  would be its first write; and the refusal test must not repeat the
  cut guard's mistakes — it needs pinned provenance for hf's layout
  constants (`.claude/rules/python-code.md` → External-reference
  provenance), a hermetic tripwire asserting against hf's own
  `get_local_download_paths` rather than a hand-built fixture, refusal
  on any unreadable subtree (`Path.rglob` swallows per-directory
  `OSError`, so an unreadable subtree silently reads as empty), and
  refusal on a symlinked creator dir (the spec 0010 escape).
- [ ] **A successful `--include` pull deletes another subset's staged
  bytes** (found by both reviewers on spec 0019, 2026-08-13;
  **pre-existing**, identical on `main`, not a regression). The
  successful-pull cleanup removes the whole staging leaf without
  asking what is in it, so a completed `--include '*Q4_K_M*'` pull
  discards a parked `*Q8_0*` download staged beside it — bytes
  `already_staged_bytes` would otherwise have counted as a resume head
  start. Reproduced both ways: `before: partial exists True |
  completed staged file exists True` → after the Q4 pull, both False.
  Deliberately left out of 0019, whose diff is one idea (a cleanup
  must not fail the pull it cleans up after). The fix is a
  disposability check on that path too, which is the same predicate
  `--clean` needs — worth doing together.
- [ ] **Hold off sleep for the duration of a long pull** (found while
  diagnosing spec 0019, 2026-08-12): the live trigger was not a flaky
  link but the host's own `DarkWake from Deep Idle` during a 5.5-hour
  overnight transfer, which dropped the SMB session twice. The payload
  survived only because the transfer client retried. A `caffeinate`
  -style assertion held for the transfer would remove a whole class of
  interrupted overnight pulls. It changes `pull`'s behavior and takes
  a platform-conditional API (`.claude/rules/python-code.md` →
  "Platform-conditional APIs" — reach it via `getattr`, and note Linux
  CI will not exercise it), so it wants its own spec.
- [ ] Pull planning errors should name the model directory (queued
  from the 0014 review round, 2026-07-31): with the plan computed
  before the grouping prompt, a changed-weight integrity stop can now
  fire before any question puts the home on screen — the error names
  the file's relative path but not which model directory holds the
  conflicting record. Add the directory to those messages.

- [ ] `--json` on the read-only reporting commands (queued
  2026-07-13, from the 0009 wrap-up: exit codes serve cron, but an
  agent/script that wants the *details* would have to parse prose
  we never promised stable). One flag, one JSON document on stdout,
  human report unchanged without it; exit codes unchanged. In value
  order: `verify` first (serialize the existing `VerifyReport` /
  `ModelVerifyResult` dataclasses — thin layer, no new logic:
  per-model `{model_id, state, problems[], unhashed[],
  unrecorded[]}` plus totals), then `status` (inventory as data —
  also the natural carrier for the future capability report), then
  `pull --plan` (lets scripts gate a pull on fit/advisories before
  committing bytes — pairs with the examples cookbook's
  non-interactive recipes). `show --json` is nearly free (emit the
  on-disk record) but low value — the record file is already JSON;
  include it only for surface consistency. `discover` stays
  human-interactive by design — no JSON there.
- [ ] Goal-definitive archiving (live-use 2026-07-13: "my goal was
  fine-tuning and I couldn't tell if I'd archived enough"). Two
  halves, both deterministic from existing data: (a) post-pull
  master *offer* in discover — when a quant pull completes and the
  full-precision master isn't archived, ask "also archive
  <master> (<size>) — needed for future fine-tuning? [y/N]" (human
  pick, never auto-add; turns the advisory into a decision point);
  (b) capability report in `status` derived from each record —
  runnable / re-quantizable (bf16+imatrix present) / fine-tunable
  (safetensors master present), with the exact missing pull named.
  The `docs/cli.md` "Archiving for a goal" table is the interim
  reference.
- [ ] Interactive listing TUI (future spec candidate; live-use
  2026-07-13): **the `discover` half of this shipped as spec 0015**
  — accumulate-paging is gone, frames are windowed to the terminal,
  and `b` steps back — so what is left here is `pull`'s file
  listing, which still has the long-scroll problem, plus the
  interaction affordances plain print cannot reach. A terminal UI —
  scrollable viewport sized to the terminal, arrow-key
  highlight-and-enter selection, optional type-to-filter — replaces
  numbered picks as presentation only; the deterministic
  facts/no-ranking invariants and the pipe-testable pick model both
  need a story (TUI harness for tests, plain fallback for dumb
  terminals). New dependency (`textual` or `prompt_toolkit`) goes
  through the dependency-hygiene skill first.
- [ ] File-kind dictionary in the listing (grew from the quant-label
  UX item; 0000 roadmap "Later"): annotate recognized quant labels
  (deterministic provenance-pinned table: bits/weight, quality tier,
  "common default" marker), tag bf16/f16 GGUFs as full-precision
  re-quantization sources, and/or `--quant` sugar. Companion-kind
  annotations (imatrix/mmproj/mtp, from the advisory rules table)
  shipped in the listing 2026-07-13 — this item is the rest of the
  dictionary. Live-use addition (2026-07-12): empty pattern input at
  the prompt errors (exit 2) instead of re-prompting. (The other
  2026-07-12 addition — human sizes in the listing — shipped
  2026-07-13.)
- [ ] Example-run cookbook (`docs/examples.md`): one worked pull per
  repo archetype — GGUF quant repo, original safetensors
  (`--whole-repo`),
  multimodal (weights + `mmproj`), sharded weights, adapter/LoRA,
  embedding/reranker, gated repo (`hf auth login`). Each example
  shows the non-interactive form (`--include` + `--model` + `--yes`)
  so scripted/cron runs have a copy-paste recipe per model type.
  The `--plan` flag belongs in every recipe as the verify step.
- [ ] `quantization` record field is never populated (artifact-level
  label extraction was never specced; per-file is likely the right
  shape now that one artifact can hold several quants).
- [ ] Artifact/format-level pruning as `remove` flags (deferred from
  spec 0010's whole-model + `--include` scope): a `--format` selector
  and, once the `quantization` field is populated per-file, `--quant`
  sugar over the same removal core. `--include 'hf-snapshot/*'` already
  expresses format removal today, so this is ergonomics, not a gap.
- [ ] Retire/tombstone mode for `remove` (deferred from 0010): delete
  payload but keep the record as archive history. Out until a live
  need shows up — 0010 read "remove/retire" as a single `remove`.
- [ ] Extend `render.clean_text`'s scrub beyond C0/C1 controls to
  Unicode bidi/format characters (U+202A–202E, U+2066–2069,
  zero-width set): hub-supplied text could visually reorder a
  rendered line (trojan-source-style display spoofing). Flagged by
  the 0007 security review as Low/theoretical; the resume hint
  itself is closed by repo-id validation, but every `clean_text`
  sink would benefit. Needs its own tests — the scrub is global
  output behavior.
