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
account and network it depends on. Brian runs local models (RTX 3090
host, Apple Silicon Mac) and wants a **curated, tested shelf** of
models that stays runnable offline, on his own storage, independent
of any hub or account. Doing this by hand (huggingface-cli invocations, hand-written
records, ad-hoc checksums) is error-prone and doesn't stay current.

A usable preserved model is more than weights: it needs the tokenizer,
config, chat template, license/model card, source URL, checksums, and
a runtime that can load it. `ollama pull` / LM Studio caches are
runtime-locked working copies, not archives.

Who it is **not** for: hoarders mirroring every model, or teams needing
multi-user archive infrastructure. This is a solo-maintained personal
preservation tool.

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
- Not a mirror-everything crawler — the shelf is curated (roughly 6–10
  models per the plan), and tiered (Tier 1 must-haves fully backed up;
  Tier 3 experiments re-downloadable).
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

- **GGUF pull** — download selected files (+ README/LICENSE) from a
  Hugging Face repo into the archive with checksums and a record.
- **HF snapshot** — full Hugging Face snapshot download for
  high-value models.
- **Verify** — re-hash the archive against records/manifests, report
  drift/bitrot (BagIt-style complete vs. valid).
- **Smoke test** — offline smoke test integration (ollama /
  llama-cli), recorded per model.
- **Cache import** — import/inventory existing Ollama and LM Studio
  caches.
- Later: runtime shelf helpers (archiving installers/builds), backup
  tiering report.
