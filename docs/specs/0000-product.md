# 0000 — llm-preserver (product spec)

**Status:** evergreen
**Last updated:** 2026-07-09

> Distilled from the vault note "Local AI Model Preservation Plan"
> (Obsidian, `Research/AI/`, 2026-07-09). That note is the design
> source; this spec is the actionable product framing. Drafted by an
> agent from that note — Brian to review and revise in place.

## Problem

A model that runs today is not guaranteed to be downloadable
tomorrow: hub repos get renamed, gated behind license/account
requirements, or removed, and a download is only as durable as the
account and network it depends on. Doing this by hand (huggingface-cli invocations, hand-written
records, ad-hoc checksums) is error-prone and doesn't stay current.

A usable preserved model is more than weights: it needs the tokenizer,
config, chat template, license/model card, source URL, checksums, and
a runtime that can load it. `ollama pull` / LM Studio caches are
runtime-locked working copies, not archives.


## Success metrics

- Every archived model can be smoke-tested offline ("Return exactly:
  READY") and the result is recorded — the archive is *tested*, not
  just downloaded.
- Each model in the archive carries a complete record: source URL,
  license, format, quantization, SHA256, download date, runtime tested.
- Restoring use of an archived model on a fresh machine requires only
  the archive (weights + metadata + a preserved runtime), no hub access.
- A quarterly refresh (add keepers, prune experiments, re-verify
  checksums) takes under an hour of human time.

## Kill criteria

- If maintaining the tool costs more time than the manual
  `huggingface-cli` + checklist workflow it replaces, stop and keep the
  checklist.
- If a mature open-source tool covers the same archive-with-records
  workflow, adopt it and archive this repo.

## Product non-goals

- Not a model *server* or inference frontend — it archives and
  verifies; Ollama/LM Studio/llama.cpp do the running.
- Not a mirror-everything crawler — models enter the archive by
  explicit user selection (exact hub repo ids), one artifact at a
  time; no discovery, search, or bulk-mirroring mode. Backup tiering
  is a user-assigned label the tool reports on (see the tiering
  report in the roadmap), not a policy it enforces.
- Not a redistribution platform — licenses are preserved and respected,
  not circumvented. Gated repos require the user's own accepted terms
  and token.
- No GUI. CLI only.

## Constraints and assumptions

- Solo-maintained, public repo, MIT-licensed, spare-time cadence.
- Python 3.12 + uv; CLI tool.
- Storage is the user's problem (NAS / big disks); the tool must be
  pointed at an archive root and never assume it fits on the boot disk.
- Model sources: Hugging Face Hub first (covers GGUF repos and full
  snapshots). Ollama/LM Studio caches are *imports*, not sources of
  record.
- Hardware targets for smoke tests: RTX 3090 (CUDA) and Apple Silicon
  (Metal/MLX) — but the archive format is runtime-independent.
- The archive layout and record format are decided in ADR 0001
  (`docs/adr/0001-model-storage.md`): a model-first filesystem tree
  (`models/<creator>/<model>/` holding all formats of a model together)
  with a per-model record — a deliberate revision of the vault plan's
  format-first sketch.

## Roadmap pointers

Drafted specs (local-only numbering; a number is consumed only when
a spec file is created, so planned features below carry names, not
numbers):

- `0001-archive-init-and-manifest.md` — archive layout, model-record
  schema, `init` / `status` commands.
- `0002-runtime-views.md` — models are too large to shuttle between
  bulk storage and local disks, so generate disposable symlink/config
  views that let runtimes run models *in place* from the (payload-
  immutable) archive — LM Studio symlink tree, llama.cpp/vLLM direct
  paths, optional best-effort Ollama linker. Ships after the download
  specs.

Planned features, spec pending (in rough priority order):

- **Selective pull** — download selected files (+ README/LICENSE)
  from a Hugging Face repo into the archive with checksums, a pinned
  commit hash, and a record. Input is an exact hub repo id — the tool
  never resolves fuzzy names ("qwen 27b"); deterministic metadata
  lookups only, no LLM in the tool. The user picks the artifacts that
  fit their hardware (e.g. one Q4_K_M file from a 20-quant GGUF repo,
  never all of them); the tool assists by listing the repo's files
  with sizes (one metadata API call) for interactive or
  `--include`-pattern selection. Canonical grouping under the
  original model's directory is inferred from the quant repo's
  `base_model` model-card metadata and confirmed with the user, with
  a `--model` override (see ADR 0001, "judgment call at download
  time").
- **Full snapshot** — full repo-tree download for high-value models
  (original safetensors weights). MLX conversions ride this same
  path — an `mlx-community/*` repo is just an HF repo landing in the
  model's `mlx/` subdirectory. The two pull specs differ by download
  *shape* (selected files vs. whole tree), not by weight format;
  format is a record field and a subdirectory (ADR 0001), so new
  formats need no new spec or code path.
- **Verify** — re-hash the archive against records/manifests, report
  drift/bitrot (BagIt-style complete vs. valid).
- **Smoke test** — offline smoke test integration (ollama /
  llama-cli), recorded per model.
- **Cache import** — import/inventory existing Ollama and LM Studio
  caches. Two halves: *inventory* (read-only scan — what the caches
  hold, what isn't archived yet) and *import* (copy into the
  archive). Caches are partial sources: LM Studio keeps real GGUF
  filenames and the HF repo id in its path but no license/model
  card/commit pin; Ollama stores digest-named blobs whose library
  models are Ollama-quantized with no hub provenance at all. Import
  therefore computes what it can locally (SHA256s, GGUF-embedded
  metadata), backfills model-level docs from the hub when the source
  repo is resolvable, and records the rest as explicit nulls with a
  per-artifact provenance flag (verified hub pull vs. unverified
  import). A fresh pull is always preferred when the model is still
  downloadable; import is the rescue path for models that no longer
  are.
- Later: runtime shelf helpers (archiving installers/builds), backup
  tiering report.
