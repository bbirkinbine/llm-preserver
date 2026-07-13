# 0000 — llm-preserver (product spec)

**Status:** evergreen
**Last updated:** 2026-07-13

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
  explicit user selection, one artifact at a time; no bulk-mirroring
  mode. Backup tiering is a user-assigned label the tool reports on
  (see the tiering report in the roadmap), not a policy it enforces.
  *Revised 2026-07-13:* the original phrasing also banned "discovery,
  search" — the real intent was **no LLM wrapper and no tool
  judgment deciding what to download**. Deterministic discovery is
  in scope: the tool may pass through the hub's own free-text search
  results and typed model-tree metadata (quantized / finetune /
  adapter / merge relations) as facts for the *human* to pick from.
  The invariant that stands: the tool never ranks by its own
  judgment, never selects, and never pulls anything without an
  explicit human pick of an exact repo id. Curated *data* tables
  with pinned provenance (quant-label facts: bits/weight, quality
  tier, common-default marker) are fine; a publisher-reputation
  table ("trust this quantizer") is not — repo download counts are
  the hub's own fact and carry that signal instead.
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
- Single upstream dependency: the hub is reached only through
  `huggingface_hub` (HF's official client) and only via the `hub.py`
  seam, so API churn has a one-module blast radius. The API is
  stable in practice but carries no compatibility contract:
  client-library drift is caught by Dependabot PRs + CI; server-side
  and metadata drift surface only at runtime (fault domain 4 /
  untrusted-metadata handling) — the live-hub canary in the roadmap
  is the early-warning gap-filler.
- Hardware targets for smoke tests: a consumer CUDA GPU and Apple
  Silicon (Metal/MLX) — but the archive format is runtime-independent.
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
- `0003-selective-pull.md` — **shipped 2026-07-11 (PR #4)**: `pull`
  downloads selected files from an exact hub repo id with checksums,
  pinned commit, and a schema-v2 record; per-file provenance,
  fault-domain errors, `LLM_PRESERVER_ARCHIVE`, `docs/cli.md`.
- `0004-full-snapshot.md` — **shipped 2026-07-12 (PR #5)**:
  whole-repo-tree download for high-value models (original
  safetensors masters; MLX rides the same path into `mlx/`). Same
  `pull` verb, different *shape*; reuses 0003's machinery. Flag
  later renamed `--all` → `--whole-repo` by 0005.
- `0005-companion-advisory-and-pull-plan.md` — **shipped
  2026-07-13**: archive-aware companion/dependency advisories
  (curated data rules, never auto-add; grouping-mismatch warning),
  `pull --plan` dry run, size confirmation + disk preflight on every
  pull mode.

Planned features, spec pending (in rough priority order):

- **Guided discovery** (0006 candidate; product stance revised
  2026-07-13, see non-goals) — the deterministic path from "I only
  know the model's name" to a completed, correctly-grouped pull
  without leaving the tool: hub free-text search passed through
  verbatim → pick the model → typed model-tree listing (derivatives
  down, parent chain up — a user landing on a finetune sees what it
  derives from) → the existing interactive file listing, annotated
  with the curated quant-label table (absorbs the "Later"
  quant-selection UX item below) plus hub facts (downloads,
  publisher, size, gated). Success bar: a person who knows only a
  model's name gets from name to a completed pull without opening a
  browser. Caveat to design for: `base_model` metadata can be stale
  (renamed upstream repos) — present it honestly, never auto-follow.
- **Live-hub canary** — a scheduled (not per-PR) CI job running a
  cheap read-only real-hub operation (`pull --plan` on one small
  known repo) so server-side API/metadata drift is an email, not a
  failed pull; CI's deterministic offline suite stays untouched.

- **Verify** — audit the archive against records/manifests,
  BagIt-style: *complete* (every recorded file exists on disk —
  catches out-of-band deletion, since the tool itself never deletes)
  and *valid* (every file re-hashes to its recorded SHA256 — catches
  bitrot and tampering). Read-only report; repair actions are the
  user's call. Pull deliberately does only a per-file existence
  check on its own skip path (spec 0003) — whole-archive drift
  detection lives here.
- **Smoke test** — offline smoke test integration (ollama /
  llama-cli), recorded per model.
- **Cache import** — import/inventory existing Ollama, LM Studio,
  and Hugging Face caches. Two halves: *inventory* (read-only scan —
  what the caches hold, what isn't archived yet) and *import* (copy
  into the archive). Caches are partial sources: the HF cache
  (`hf download` / `huggingface_hub`) is the richest — its layout
  preserves repo id and commit hash, though hashes were never
  verified by us and license/docs are only present if the user
  pulled them; LM Studio keeps real GGUF filenames and the HF repo
  id in its path but no license/model card/commit pin; Ollama stores
  digest-named blobs whose library models are Ollama-quantized with
  no hub provenance at all. Import
  therefore computes what it can locally (SHA256s, GGUF-embedded
  metadata), backfills model-level docs from the hub when the source
  repo is resolvable, and records the rest as explicit nulls with a
  per-artifact provenance flag (verified hub pull vs. unverified
  import). A fresh pull is always preferred when the model is still
  downloadable; import is the rescue path for models that no longer
  are.
- Later: quant-selection UX sugar — the interactive listing should
  annotate recognized quant labels (deterministic lookup table:
  bits/weight, quality tier, "common default" marker) and/or a
  `--quant Q4_K_M` flag as sugar over `--include`; first live use
  produced a Q4_0-instead-of-Q4_K_M mispull (2026-07-11) because the
  labels alone are indistinguishable to a non-expert. *Update
  2026-07-13:* the label table is absorbed into guided discovery
  (above); the companion-file warning half shipped in 0005 as the
  generalized advisory rules. Runtime shelf
  helpers (archiving installers/builds), backup tiering report,
  managed remove/retire (the only sanctioned way to
  delete from the archive — updates record and directory together,
  with confirmation; hand-deletion is what verify exists to catch).
