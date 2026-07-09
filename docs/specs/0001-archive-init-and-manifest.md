# 0001 — Archive init and model-record manifest

**Status:** draft
**Last updated:** 2026-07-09

## Goal

Create the foundation every later feature builds on: a CLI
(`llm-preserver`) that can initialize a model-archive directory tree
and read/write per-model records. This makes the archive layout and
record schema concrete *before* any downloading exists, so the later
feature specs (GGUF pull, HF snapshot, verify, smoke test — see the
roadmap in `0000-product.md`) all write into the same structure. See `0000-product.md` for the product rationale; the
layout and record-format decision itself is ADR 0001
(`docs/adr/0001-model-storage.md`), which must be accepted before this
spec is implemented.

## Success criteria

- `llm-preserver init <path>` creates the archive skeleton — the
  model-first layout from ADR 0001 (`docs/adr/0001-model-storage.md`):
  `models/`, `runtimes/`, `manifests/` plus the versioned
  `archive.json` root marker — and is idempotent — re-running on an
  existing archive changes nothing and exits 0.
- `init` on a non-empty directory that is *not* an archive refuses
  with a clear error (never adopts or modifies unknown data).
- A model record (Pydantic model, serialized as `model-record.json`
  in the model's directory, with a generated human-readable
  `MODEL-RECORD.md` rendering — see ADR 0001) captures, at the model
  level: name, original hub id, role (chat/coding/embedding/reranker/
  multimodal), license, parameter count, context length, notes; and
  per artifact (a model holds one or more formats — GGUF, HF snapshot,
  MLX): format, quantization, source repo URL, pinned hub revision
  (full commit hash, not a branch name), download date, per-file
  SHA256 + size + an original-vs-generated flag, runtime/hardware
  tested (see ADR 0001's prior-art conventions). Unknown-at-
  download-time fields are explicitly nullable, not omitted.
- `llm-preserver status <path>` walks `models/` and prints an
  inventory table: each model, its archived formats, role, record
  completeness (missing license? missing checksum?), and sizes.
- All commands operate only on the given archive path; nothing is
  written outside it. Tests exercise everything in `tmp_path`.

## Non-goals

- No downloading, no hashing of preexisting files beyond reading
  records, no smoke tests, no Ollama/LM Studio import, no runtime
  views — each is its own later spec (see the `0000-product.md`
  roadmap).
- No global config file or default archive location yet — the archive
  path is always an explicit argument.
- No concurrency, no daemon, no progress bars.

## External references

- **None — original.** The layout and record fields come from Brian's
  own vault note ("Local AI Model Preservation Plan", Obsidian
  `Research/AI/`, 2026-07-09), which is a design input, not an external
  authority. No outside registry or spec constrains correctness here.
  (The download specs will cite `huggingface_hub` API docs as an
  authoritative source; not needed for this spec.)

## Sketch

Typer CLI (`llm-preserver = llm_preserver.cli:app`), a `records`
module (Pydantic v2 `ModelRecord` with nested artifact entries + JSON
round-trip + markdown rendering), and an `archive` module (skeleton
creation, marker file, inventory walk). Layout, record placement, and
the versioned `archive.json` marker are decided by ADR 0001
(`docs/adr/0001-model-storage.md`) — this spec implements them, it
does not relitigate them.
