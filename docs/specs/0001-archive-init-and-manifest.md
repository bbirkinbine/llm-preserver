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
  level: name, original hub id, roles (nonempty list drawn from
  chat/coding/embedding/reranker/multimodal — curator judgment, first
  entry is the primary role; a model may serve several), capabilities
  (machine-derived free-string list — e.g.
  tools/vision/thinking/embedding — populated from source metadata by
  the download/import features; null until then), the source repo's
  `pipeline_tag` verbatim (null until recorded), license, parameter
  count, context length, notes; and
  per artifact (a model holds one or more formats — GGUF, HF snapshot,
  MLX): format, quantization, source repo URL, pinned hub revision
  (full commit hash, not a branch name), download date, per-file
  SHA256 + size + an original-vs-generated flag, runtime/hardware
  tested (see ADR 0001's prior-art conventions), and a provenance
  flag distinguishing a verified hub pull (hashes match a pinned
  revision) from an unverified cache import (see the cache-import
  feature in the `0000-product.md` roadmap). Unknown-at-
  download-time fields are explicitly nullable, not omitted.
- `llm-preserver status <path>` walks `models/` and prints an
  inventory table: each model, its archived formats, role, record
  completeness (missing license? missing checksum?), and sizes.
- A per-model detail view — `status <path> <creator>/<model>` or a
  separate `show` command (decide at plan time) — prints everything
  archived for one model: each artifact's format, quantization,
  source repo, pinned revision, size, and provenance flag.
- All commands operate only on the given archive path; nothing is
  written outside it. Tests exercise everything in `tmp_path`.
- Review-time decisions (Brian, 2026-07-09): read-only commands
  (`status`, `show`) also refuse a missing/invalid marker or a newer
  `schema_version`, per ADR 0001's refusal rule; `save_record` writes
  `MODEL-RECORD.md` together with the JSON so they cannot desync
  (`show` renders without the generated-file header); unknown record
  fields survive load/re-save (`extra="allow"`) so an older tool never
  destroys a newer record's data; the record carries its own
  `record_schema_version` so a lone copied model dir stays
  self-describing; `role` became `roles` (a model serves multiple
  purposes) with separate machine-fact seats `capabilities` and
  `pipeline_tag`, distinguishing curator judgment from derivable
  metadata. A record claiming a *newer* `record_schema_version` is
  flagged, not refused (`status` completeness column; `show` warns to
  stderr but renders) — read-only inspection stays useful. Role and
  format vocabularies stay strict `Literal`s: an unknown value fails
  validation and the record degrades to a visible `record unreadable`
  state (with a newer-schema hint when the record claims one); the
  on-disk JSON is never touched, so nothing is lost.

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
