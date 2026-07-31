# 0002 — Runtime views

**Status:** in progress
**Last updated:** 2026-07-30
**Depends on:** 0001

## Goal

Generate disposable, tool-specific "views" — symlinks and config —
that let inference runtimes run models *in place* from the archive,
because models are too large to shuttle between bulk storage and
local disks. The archive stays the single, payload-immutable copy
(ADR 0001 → "Runtime consumption: views, not copies" owns that
principle); this spec owns the per-tool mechanics. It ships after the
download specs exist, since views need models to point at — the
number is an identifier, not an execution order.

## Phasing (2026-07-30)

Phase 1 (branch `spec-0002-runtime-views`): the shared `views` core
(record-driven source scan, dest preflight + generated marker) plus
the **Ollama adapter only** — Ollama is the daily-driver runtime, so
it lands first even though it is the best-effort mode. LM Studio,
llama.cpp, and vLLM adapters are later phases on this same spec, each
one adapter module + tests over the same core. Phase boundaries get a
`## Phase handoff` section per WORKFLOW.md.

## Success criteria

- `llm-preserver views <archive> --tool lm-studio --dest <dir>`
  builds a local view tree of `<publisher>/<model>/<file>.gguf`
  symlinks into the archive, matching LM Studio's required two-level
  layout. Re-running refreshes the tree; deleting it loses nothing.
- `llm-preserver views <archive> --tool llama-cpp` (and vLLM
  equivalent) prints/exports the direct archive paths per model —
  these tools need no links, only path discovery.
- An Ollama mode exists only as explicitly-labeled **best effort**
  (phase 1 mechanics, revised 2026-07-30 after the gating live test —
  see External references): the default output is instructions for
  the supported copy-based `ollama create` import. An opt-in
  `--seed-store` flag instead seeds a complete external store at
  `--dest`: `blobs/sha256-<digest>` symlinks built from the SHA256s
  already in the records (no re-hashing; digest = SHA256 of file
  bytes, verified against Ollama source 2026-07-30), plus a
  **tool-synthesized** minimal config blob and manifest per minted
  name — the fallback design, promoted to primary after the
  seed-and-delegate design failed its gating test (`ollama create`
  rewrites GGUF layers into a new full-size blob, so delegation
  always copies). The synthesized store was live-verified 2026-07-30
  on ollama 0.32.0: `ollama list` shows the models and the runtime
  loads and serves through the symlink with zero payload bytes
  copied (12 KB store over a 1.15 GB model, end-to-end through the
  real CLI). A loud warning states Ollama does not support external
  stores; printed instructions cover `OLLAMA_MODELS=<dest>`,
  `OLLAMA_NOPRUNE=1`, and that `ollama serve` reads the env at
  startup. The tool never runs Ollama itself. Known caveat: no
  template/params layers are synthesized — chat-template fidelity
  for generate-class models is untested (the live test used an
  embedding model) and is called out in `docs/cli.md`.
- Eligibility is per-model and reported, never silent: Ollama views
  cover GGUF artifacts only. The command prints a breakdown (scanned /
  eligible / skipped with a reason per skip: safetensors-only,
  unhashed, sharded GGUF — sharded is skipped in phase 1). Zero
  eligible models: report, write nothing (no dest tree, no marker),
  exit 1. A refresh whose eligible set shrank to zero reports the
  same and leaves the existing view untouched.
- Model names in the view are minted deterministically from the
  archive's creator/model layout plus file identity (shaped near the
  `hf.co/<org>/<repo>:<quant>` form) — no ranking, no judgment.
- Views never write into the archive, and view generation works
  against a read-only archive mount.
- Every view file/dir is identifiable as generated (naming or marker)
  so nothing mistakes a view for archived data.

## Non-goals

- Not a model server or launcher — views make runtimes *able* to find
  the archive; starting/serving models stays the runtime's job.
- No hot-cache management (copying frequently-used models to local
  disk is the human's call; the design permits it, this spec doesn't
  automate it).
- No contorting the archive layout to match any single tool (rejected
  in ADR 0001).

## External references

Per-tool feasibility verified 2026-07-09 (research session; moved
here from ADR 0001):

- **llama.cpp — direct path.** `llama-cli`/`llama-server` `-m` takes
  any path, read-only OK, mmap by default. Source:
  <https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>.
  Community-practice caveat (not official guidance): mmap page-in
  over NFS/SMB can be slow or hang on very large models; `--no-mmap`
  or direct-I/O are the mitigations.
- **vLLM — direct path.** `vllm serve <hf-snapshot-dir>` with
  `HF_HUB_OFFLINE=1`; matches the archive's `hf-snapshot/` contents.
- **LM Studio — config redirect + file-level symlinks.** Models dir
  is user-changeable; layout must be exactly
  `<publisher>/<model>/<file>.gguf`. File-level symlinking is
  officially supported via `lms import --symbolic-link` (flags seen
  via search snippets of
  <https://lmstudio.ai/docs/cli/local-models/import>; page not fully
  fetched). **Warning:** bare `lms import` *moves* the file — never
  run it against the archive. Layout source:
  <https://lmstudio.ai/docs/app/advanced/import-model>.
- **Ollama — no supported in-place mode.** `OLLAMA_MODELS` requires
  read-write access (Ollama FAQ, search snippet); `ollama create
  FROM /path.gguf` copies across filesystems
  (<https://docs.ollama.com/import> is silent; behavior per community
  sources). Blob-symlink workaround (`blobs/sha256-<digest>` →
  external file) is community-known and unsupported:
  <https://github.com/ollama/ollama/issues/1981> (feature request,
  closed unimplemented). Cross-tool linking practice:
  <https://www.rushis.com/sharing-local-llm-models-between-ollama-and-llama-cpp/>.

**Unverified — must be tested during implementation, not assumed:**
LM Studio dir-level symlinks and read-only models-dir tolerance;
llamafile/mlx-lm path handling. Re-verify all tool behaviors at
implementation time — these tools version fast and this spec's
research will be stale.

Ollama re-verification, 2026-07-30 (current docs, `main` source at
v0.32.x era, issue tracker):

- Still no official external-model support; multi-path storage
  request open unimplemented
  (<https://github.com/ollama/ollama/issues/12729>). `FROM <path>`
  still full-copies — a symlinked source is resolved then copied
  (`cmd/cmd.go` `createBlob`); no hard-link path.
- Blob layout unchanged: `$OLLAMA_MODELS/blobs/sha256-<64 hex>`,
  digest = SHA256 of the file bytes (`manifest/layer.go`), manifests
  at `manifests/<registry>/<namespace>/<model>/<tag>`. Watch item:
  PR <https://github.com/ollama/ollama/pull/15735> ("manifest-v2",
  open/unmerged 2026-07-30) would restructure the manifest tree and
  rename the default registry — pin layout constants with provenance.
- Symlinked blobs work incidentally (read path follows them; nothing
  rejects them) but remain unsupported
  (<https://github.com/ollama/ollama/issues/1981>, closed
  unimplemented). Startup prune `os.Remove`s blobs unreferenced by
  any manifest (link deleted, external target untouched);
  manifest-referenced blobs survive; `OLLAMA_NOPRUNE=1` disables the
  startup pass (`envconfig/config.go`).
- `ollama list` is purely local manifest enumeration — no network,
  no pull required.
- `OLLAMA_MODELS` officially requires read-write
  (<https://docs.ollama.com/faq>); the view store, not the archive,
  is what Ollama gets pointed at.
- **Phase-1 gating live test — run 2026-07-30, FAILED, fallback
  adopted.** On ollama 0.32.0 with a real 1.15 GB GGUF and a seeded
  blob symlink: `ollama create` marks GGUF layers
  `rewriteForCreate: true` (`server/create.go`) and rewrites them via
  `ggml.WriteGGUF` into a **new full-size blob under a new digest**
  unknowable from the record — the manifest references the rewritten
  blob, the seeded symlink ends up manifest-unreferenced, and the
  store grows by the full model size (seeding merely halves create's
  copying: one rewritten copy instead of raw-upload + rewrite). The
  spec's fallback — tool-synthesized manifests — was implemented and
  live-verified the same day: a manifest (`schemaVersion: 2`, docker
  media types, `sha256:<hex>` digests, mirrored from a real 0.32.0
  store inspected on this machine) plus a minimal config blob
  (`model_format`/`architecture`/`os`/`rootfs.diff_ids`) over the
  symlinked blob **lists and serves** — real embeddings returned
  through the seeded symlink, zero payload bytes copied, verified
  end-to-end through the real CLI against a temp store. Untested
  remainder: chat-template fidelity for generate-class models (no
  template layer is synthesized; the live test used an embedding
  model).

## Sketch

A `views` module with one small adapter per tool, each consuming the
archive records (never re-hashing payloads). Symlink targets use
absolute paths into the archive mount; document the stable-mount-point
assumption. Tests build a fake archive in `tmp_path` and assert view
shape, link targets, and that the archive tree is untouched
(read-only bit respected).
