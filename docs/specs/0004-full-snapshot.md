# 0004 — Full Snapshot

**Status:** shipped
**Last updated:** 2026-07-12

> Historical note: this spec's `--all` flag was renamed to
> `--whole-repo` by spec 0005 (semantics unchanged). Flag references
> below are the surface as originally shipped.

## Goal

Archive the *master copy* of a model: download a Hugging Face repo's
whole tree — original full-precision safetensors, config, tokenizer,
everything — into the archive as one artifact. Selective pull (spec
0003, shipped) acquires runnable derivatives; this spec acquires the
source they derive from, which is what carries the archival value:
quantization is one-way lossy, so the original is the only copy that
can be re-quantized into formats that do not exist yet, fine-tuned,
merged, or loaded by non-GGUF stacks (vLLM, MLX). Archival of
originals is the product's primary goal (Brian, 2026-07-11); in the
0000 preservation-plan terms this is the Tier-1 path — masters for
the models you'd mourn, quants for the road. The two download specs
differ by *shape* only (whole tree vs. selected files); this spec
reuses 0003's machinery end to end: the `hub.py` client seam,
stage → hash → move, record-written-last, per-file provenance and
revision, fault domains, idempotent skip, payload locking, and the
`--refresh-docs` escape hatch semantics.

## Success criteria

- `llm-preserver pull <repo-id> --all [PATH]` downloads every file in
  the repo at the pinned commit into the model's format subdirectory,
  preserving repo-relative paths (sharded weights like
  `model-00001-of-00012.safetensors` land beside `config.json` and
  the tokenizer files — a safetensors tree is only runnable as a
  tree). (`--all` on the existing `pull` verb, mutually exclusive
  with `--include` — decided 2026-07-11.)
- Grouping inverts for original repos: with no `base_model` card
  metadata, the canonical model directory *defaults to the repo id
  itself*, confirmed interactively (`--model` still overrides; the
  0003 hard stop applies only when metadata exists but is unusable).
  Pulling `Qwen/Qwen3.6-27B --all` therefore needs no `--model` —
  this removes the hoop found in live use (2026-07-11). Ratified at
  plan review (Brian, 2026-07-11): the repo-id default applies to
  `pull` *generally*, not only under `--all` — one grouping rule,
  confirm-gated, amending 0003's no-metadata hard stop.
- Format subdirectory: `hf-snapshot/` for original trees;
  `mlx-community/*` (and other MLX-format) repos ride the same path
  into the model's `mlx/` subdirectory (0000 roadmap — an MLX repo is
  just an HF repo landing in a different format slot). GGUF-repo
  snapshots infer `gguf/` as in 0003.
- A snapshot preserves the tree verbatim — README/LICENSE stay at
  their in-tree paths rather than moving to the 0003
  `docs/<source-repo>/` subdirectory, because the tree itself is the
  artifact and in-tree docs cannot collide across repos (each
  snapshot owns its format subdirectory). Decided 2026-07-11:
  tree fidelity wins for the snapshot shape; 0003's doc relocation
  continues to apply to selective pulls only.
- Disk-space preflight: file sizes are already in the one metadata
  call; the tool sums them, compares against free space at the
  archive path, and refuses (local-environment domain) when the tree
  will not fit — before any bytes download. The total size is shown
  in the confirmation prompt either way, since whole-tree pulls are
  routinely 50–500GB. (Hard refusal, not a warning, stating required
  vs. available; size + file-count confirmation on every `--all`
  pull — decided 2026-07-11.)
- The artifact is recorded per 0003 schema v2 with
  `format: hf-snapshot` (or `mlx`/`gguf`), `quantization: null`,
  pinned commit, per-file provenance and revision; hub facts
  (`pipeline_tag`, license) recorded as in 0003. A later selective
  pull of a quant repo merges into the same model record as a second
  artifact — verified live in 0003, unchanged here.
- Idempotency, interrupted-pull retry, integrity hard stops, payload
  locking, manifest coverage, and fault-domain exit codes behave
  exactly as spec 0003 defines — the whole-tree shape adds no new
  states, and re-running a partially completed snapshot resumes by
  skipping completed files.
- Tests hit no network (fake hub client at the seam); a live
  verification against a small real repo is part of the Verify phase.
- `docs/cli.md` gains the snapshot workflow (usage docs ride the
  feature).

## Non-goals

- **Selecting files** — that is spec 0003; `--all` and `--include`
  are mutually exclusive.
- **Converting formats** — the tool archives what the hub serves; it
  never runs quantizers/converters.
- **Verify, cache import, runtime views, managed remove** — separate
  roadmap items.
- **Non-HF hubs** — same HF-only decision as 0003.
- **Bandwidth/dedup optimization** (Xet chunk reuse across quants) —
  the client does what it does; we do not build transfer tooling.
- **Auto-selecting which models deserve snapshots** — tiering is
  curator judgment (0000 plan); the tool only makes the master-copy
  pull cheap.

## Notes

- Depends on spec 0003 (shipped 2026-07-11, PR #4) — this is a reuse
  spec; the plan should confirm how much of `pull.py`/`pull_plan.py`
  is touched at all vs. parameterized.
- Gated originals (Llama-style license acceptance) work through the
  same ambient-token posture as 0003 — accept the license on the hub
  once, `hf auth login`, no tool changes.
- Sharded-weight repos are large in file *count* too (a 70B repo can
  ship 30+ shards plus index JSONs); the staging directory and
  manifest already handle N files, but the plan should sanity-check
  the interactive confirmation copy for whole-tree pulls (listing
  500 files is noise; total size + file count is the signal).
- Decided (Brian, 2026-07-11): `--all` skips the per-file listing —
  the selection *is* the tree — and confirms with total size + file
  count only. Download progress is not lost: the client's native
  per-file progress bars stay enabled (default behavior; what live
  pulls already show in a terminal), and the tool logs each file at
  INFO with an `n of m` counter so non-TTY runs still show which
  shard is in flight. Progress bars are the client's concern; we
  build no progress UI of our own. Ratified at plan review (Brian,
  2026-07-11): the `n of m` INFO line logs on *all* pulls, not only
  `--all` — one download loop, no mode flag.

## Review adjudications (2026-07-11, Brian)

Four review-round findings challenged spec decisions; ruled:

- **One source repo per format subdirectory per model.** A second
  same-format snapshot from a *different* source repo is refused
  with an honest message (archive under a different `--model` home
  or as a selective pull) — this makes "each snapshot owns its
  format subdirectory" true in code, keeps tree fidelity, and makes
  `--refresh-docs` provably safe (every recorded path in a subdir
  belongs to one artifact). Same-repo selective + `--all` mixing
  stays additive (tested, both orders).
- **Grouping is format-directed.** GGUF/MLX trees are *conversions*
  — same weights, different container — and group under
  `base_model` (0003 behavior). `hf-snapshot` trees with a
  `base_model` are *derived models* — different weights — and
  default to their own repo id as canonical home, confirm-gated,
  with `base_model` mentioned as lineage in the prompt, never used
  as the home. Found live: `Qwen/Qwen3-0.6B` (instruct) filed under
  `Qwen3-0.6B-Base` before this rule.
- **Non-TTY prompts become deterministic stops.** When stdin is not
  interactive, confirmations convert to `PullUserError` (exit 2)
  naming the bypass; a `--yes` flag is added for the size
  confirmation. Scripted pulls never die with an undocumented
  exit 1.
- **Plan → preflight → confirm, showing remaining.** The `--all`
  confirmation states what will actually download ("n of m files,
  X GiB; k already archived/staged"), and an over-budget pull is
  refused before the user is asked to confirm it.

## External references

No new external APIs. The disk-space preflight uses the Python 3.12
standard library (`shutil.disk_usage`) — stdlib, no provenance pin
needed. All Hugging Face hub API facts (the single metadata call,
download/staging behavior, exception hierarchy) are inherited
unchanged from spec 0003 — see
[`0003-selective-pull.md`](0003-selective-pull.md) → "External
references" (retrieved 2026-07-10 against `huggingface_hub` 1.23.0).
